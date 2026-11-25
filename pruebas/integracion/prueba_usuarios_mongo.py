"""H-25 · Roles de base de datos contra un MongoDB con autenticacion de verdad.

El nodo del CI (y el de `pytest -m mongo` a secas) corre SIN autenticacion, asi que esta
prueba levanta el suyo: un contenedor `mongo:8` con el mismo punto de entrada que usa
Compose (`infra/mongo-entrada.sh`, que agrega `--keyFile`) y el mismo script de usuarios
(`scripts/semilla/04-usuarios.js`), en el mismo orden que `python tareas.py asegurar`:
primero sin keyfile para crear los usuarios, despues con keyfile. Solo necesita Docker.

Sostiene lo que H-25 promete:

- sin credenciales no se lee nada;
- centinela_analista lee y no escribe;
- centinela_app hace lo que hace la aplicacion (leer, escribir, transacciones, `$merge`,
  change stream, `$indexStats`, `explain`) y no puede crear indices, borrar colecciones
  ni bases, tocar usuarios ni ver otras bases;
- centinela_admin si administra esquema y usuarios, y solo en su base;
- el script de usuarios se puede correr de nuevo (con el admin, ya con la auth encendida).

Las pruebas comparten un nodo y corren en el orden del archivo; la ultima borra la base.
"""

from __future__ import annotations

import base64
import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from pruebas.conftest import saltar_o_fallar

pytestmark = pytest.mark.mongo

RAIZ = Path(__file__).resolve().parents[2]
BASE = "antifraude"
NO_AUTORIZADO = 13  # Unauthorized
AUTENTICACION_FALLIDA = 18  # AuthenticationFailed

VARIABLES = {
    "centinela_app": "MONGO_CLAVE_APP",
    "centinela_analista": "MONGO_CLAVE_ANALISTA",
    "centinela_admin": "MONGO_CLAVE_ADMIN",
    "centinela_pruebas": "MONGO_CLAVE_PRUEBAS",
    "centinela_respaldo": "MONGO_CLAVE_RESPALDO",
}
CLAVES = {usuario: secrets.token_urlsafe(16) for usuario in VARIABLES}
PUERTO_INTENTOS = 3
# Docker avisa asi cuando el puerto publicado ya lo tiene otro proceso (segun el sistema).
MARCAS_PUERTO_OCUPADO = ("address already in use", "port is already allocated", "bind for")


def _docker(*argumentos: str, tiempo: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *argumentos],
        capture_output=True,
        text=True,
        timeout=tiempo,
        check=False,
    )


def _puerto_libre() -> int:
    with socket.socket() as sonda:
        sonda.bind(("127.0.0.1", 0))
        return int(sonda.getsockname()[1])


def _puerto_ocupado(salida: str) -> bool:
    """Docker avisa asi que el puerto publicado ya lo tiene otro proceso. El texto depende
    del sistema, por eso se buscan varias formas."""
    minuscula = salida.lower()
    return any(marca in minuscula for marca in MARCAS_PUERTO_OCUPADO)


class _PuertoOcupado(Exception):
    """Otro proceso tomo el puerto entre la sonda y el `docker run`."""


def _nombre_nodo() -> str:
    return f"centinela-roles-{os.getpid()}"


def _correr_script_usuarios(puerto: int, *, como_admin: bool) -> None:
    """04-usuarios.js dentro del contenedor, con las claves por variable de entorno."""
    entorno: list[str] = []
    for usuario, variable in VARIABLES.items():
        entorno += ["-e", f"{variable}={CLAVES[usuario]}"]
    if como_admin:
        uri = (
            f"mongodb://centinela_admin:{quote(CLAVES['centinela_admin'], safe='')}"
            f"@localhost:{puerto}/?authSource={BASE}"
        )
    else:
        uri = f"mongodb://localhost:{puerto}/"
    resultado = _docker(
        "exec", *entorno, _nombre_nodo(), "mongosh", uri, "--quiet",
        "--file", "/semilla/04-usuarios.js",
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr


def _cliente(usuario: str | None, puerto: int) -> MongoClient[Any]:
    credenciales = f"{usuario}:{quote(CLAVES[usuario], safe='')}@" if usuario else ""
    autenticacion = f"&authSource={BASE}" if usuario else ""
    return MongoClient(
        f"mongodb://{credenciales}localhost:{puerto}/?replicaSet=rsfraude"
        f"{autenticacion}&serverSelectionTimeoutMS=15000"
    )


def _limpiar_nodo(nombre: str) -> None:
    _docker("rm", "-f", nombre)
    _docker("volume", "rm", "-f", nombre)


def _arrancar(nombre: str, secretos: Path, puerto: int, *, sin_auth: bool) -> None:
    _docker("rm", "-f", nombre)
    entorno = ["-e", "CENTINELA_SIN_AUTH=1"] if sin_auth else []
    resultado = _docker(
        "run", "-d", "--name", nombre, *entorno,
        "-v", f"{nombre}:/data/db",
        "-v", f"{RAIZ / 'infra' / 'mongo-entrada.sh'}:/entrada/mongo-entrada.sh:ro",
        "-v", f"{secretos}:/secretos:ro",
        "-v", f"{RAIZ / 'scripts' / 'semilla'}:/semilla:ro",
        "-p", f"127.0.0.1:{puerto}:{puerto}",
        "--entrypoint", "bash",
        "mongo:8", "/entrada/mongo-entrada.sh",
        "mongod", "--replSet", "rsfraude", "--port", str(puerto), "--bind_ip_all",
    )
    if resultado.returncode == 0:
        return
    if _puerto_ocupado(resultado.stderr):
        raise _PuertoOcupado(resultado.stderr)
    saltar_o_fallar(f"no se pudo levantar el nodo de Mongo: {resultado.stderr}")


def _esperar(nombre: str, puerto: int, consulta: str, esperado: str, segundos: int = 90) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        salida = _docker(
            "exec", nombre, "mongosh", "--port", str(puerto), "--quiet", "--eval", consulta
        )
        if salida.returncode == 0 and salida.stdout.strip() == esperado:
            return
        time.sleep(2)
    raise AssertionError(f"el nodo no llego a '{esperado}' ({consulta})")


def _montar_nodo(nombre: str, secretos: Path, puerto: int) -> None:
    """Dos fases, igual que `python tareas.py asegurar`: primero sin keyfile para crear los
    usuarios, despues con keyfile y la autenticacion encendida."""
    _arrancar(nombre, secretos, puerto, sin_auth=True)
    _esperar(nombre, puerto, "db.adminCommand({ping: 1}).ok", "1")
    _docker(
        "exec", nombre, "mongosh", "--port", str(puerto), "--quiet", "--eval",
        f'rs.initiate({{_id: "rsfraude", members: [{{_id: 0, host: "localhost:{puerto}"}}]}})',
    )
    _esperar(nombre, puerto, "db.hello().isWritablePrimary", "true")
    _correr_script_usuarios(puerto, como_admin=False)

    _arrancar(nombre, secretos, puerto, sin_auth=False)  # ahora con keyfile: auth encendida
    _esperar(nombre, puerto, "db.adminCommand({ping: 1}).ok", "1")
    limite = time.monotonic() + 90
    while True:
        try:
            with _cliente("centinela_admin", puerto) as admin:
                admin[BASE]["prueba_roles"].drop()
                admin[BASE].create_collection("prueba_roles")
                admin[BASE].create_collection("prueba_roles_destino")
                admin[BASE]["prueba_roles"].insert_one({"_id": "base", "valor": 1})
            return
        except Exception:
            if time.monotonic() > limite:
                raise
            time.sleep(2)


@pytest.fixture(scope="module")
def nodo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    """Nodo unico con keyfile y los usuarios de H-25. Devuelve el puerto.

    Si Docker no puede publicar el puerto (otro proceso lo tomo justo despues de la sonda),
    se borra lo que quedo a medias, junto con el volumen, y se reintenta con otro puerto,
    hasta PUERTO_INTENTOS veces.
    """
    if shutil.which("docker") is None:
        saltar_o_fallar("docker no esta en el PATH; la prueba de roles levanta su propio nodo")

    nombre = _nombre_nodo()
    secretos = tmp_path_factory.mktemp("secretos")
    keyfile = secretos / "keyfile"
    keyfile.write_text(base64.b64encode(secrets.token_bytes(756)).decode() + "\n")
    keyfile.chmod(0o400)

    try:
        for intento in range(1, PUERTO_INTENTOS + 1):
            puerto = _puerto_libre()
            try:
                _montar_nodo(nombre, secretos, puerto)
                break
            except _PuertoOcupado as error:
                _limpiar_nodo(nombre)
                if intento == PUERTO_INTENTOS:
                    saltar_o_fallar(
                        f"el puerto estuvo ocupado en {PUERTO_INTENTOS} intentos: {error}"
                    )
        yield puerto
    finally:
        _limpiar_nodo(nombre)


def _denegado(accion: Callable[[], Any]) -> None:
    with pytest.raises(OperationFailure) as error:
        accion()
    assert error.value.code == NO_AUTORIZADO, error.value


def prueba_sin_credenciales_no_se_lee_nada(nodo: int) -> None:
    with _cliente(None, nodo) as anonimo:
        _denegado(lambda: anonimo[BASE]["prueba_roles"].find_one())


def prueba_credenciales_equivocadas_no_entran(nodo: int) -> None:
    mal = MongoClient(
        f"mongodb://centinela_app:incorrecta@localhost:{nodo}/?replicaSet=rsfraude"
        f"&authSource={BASE}&serverSelectionTimeoutMS=15000"
    )
    with mal, pytest.raises(OperationFailure) as error:
        mal[BASE]["prueba_roles"].find_one()
    assert error.value.code == AUTENTICACION_FALLIDA


def prueba_el_analista_lee_y_no_escribe(nodo: int) -> None:
    with _cliente("centinela_analista", nodo) as analista:
        coleccion = analista[BASE]["prueba_roles"]
        assert coleccion.find_one({"_id": "base"})["valor"] == 1
        _denegado(lambda: coleccion.insert_one({"_id": "x"}))
        _denegado(lambda: coleccion.update_one({"_id": "base"}, {"$set": {"valor": 2}}))
        _denegado(lambda: coleccion.delete_one({"_id": "base"}))
        _denegado(lambda: coleccion.create_index("valor"))
        _denegado(lambda: analista[BASE].drop_collection("prueba_roles"))
        _denegado(lambda: analista.drop_database(BASE))
        assert coleccion.find_one({"_id": "base"})["valor"] == 1


def prueba_la_app_hace_lo_que_hace_la_aplicacion(nodo: int) -> None:
    with _cliente("centinela_app", nodo) as app:
        origen = app[BASE]["prueba_roles"]
        destino = app[BASE]["prueba_roles_destino"]

        # lectura y escritura
        origen.insert_one({"_id": "a", "valor": 10})
        origen.update_one({"_id": "a"}, {"$set": {"valor": 11}})
        assert origen.find_one({"_id": "a"})["valor"] == 11

        # transaccion multi-documento (H-14: resolver una alerta y su historial)
        with app.start_session() as sesion, sesion.start_transaction():
            origen.update_one({"_id": "a"}, {"$set": {"valor": 12}}, session=sesion)
            destino.insert_one({"_id": "t", "valor": 12}, session=sesion)
        assert destino.find_one({"_id": "t"})["valor"] == 12

        # `$merge` (indicadores_diarios, H-22)
        list(
            origen.aggregate(
                [
                    {"$project": {"valor": 1}},
                    {
                        "$merge": {
                            "into": "prueba_roles_destino",
                            "on": "_id",
                            "whenMatched": "replace",
                            "whenNotMatched": "insert",
                        }
                    },
                ]
            )
        )
        assert destino.count_documents({}) >= 2

        # change stream (SSE y disparador D1)
        with origen.watch() as flujo:
            origen.insert_one({"_id": "flujo", "valor": 1})
            assert flujo.next()["documentKey"]["_id"] == "flujo"
        origen.delete_one({"_id": "flujo"})

        # `$indexStats` y `explain` (red de mulas, H-21)
        assert list(origen.aggregate([{"$indexStats": {}}]))
        explicacion = app[BASE].command(
            "explain", {"find": "prueba_roles", "filter": {"_id": "a"}}, verbosity="queryPlanner"
        )
        assert explicacion["ok"] == 1
        assert "prueba_roles" in app[BASE].list_collection_names()

        # y NO puede lo que no le toca
        _denegado(lambda: origen.create_index("valor"))
        _denegado(lambda: app[BASE].drop_collection("prueba_roles_destino"))
        # (crear una coleccion si lo permite: `insert` incluye la creacion implicita)
        _denegado(lambda: app[BASE].command({"collMod": "prueba_roles", "validator": {}}))
        _denegado(lambda: app.drop_database(BASE))
        _denegado(
            lambda: app[BASE].command(
                "createUser", "intruso", pwd="x", roles=[{"role": "read", "db": BASE}]
            )
        )
        _denegado(
            lambda: app[BASE].command(
                "grantRolesToUser", "centinela_app", roles=[{"role": "dbOwner", "db": BASE}]
            )
        )
        _denegado(lambda: app["otra_base"]["cosas"].find_one())
        _denegado(lambda: app["otra_base"]["cosas"].insert_one({"x": 1}))


def prueba_el_script_de_usuarios_se_puede_repetir_con_el_admin(nodo: int) -> None:
    """Idempotente, y ya con la autenticacion encendida: lo corre centinela_admin."""
    _correr_script_usuarios(nodo, como_admin=True)
    # la app sigue entrando tras recrear su rol, y con los mismos limites
    with _cliente("centinela_app", nodo) as app:
        assert app[BASE]["prueba_roles"].find_one({"_id": "base"}) is not None
        _denegado(lambda: app[BASE]["prueba_roles"].create_index("valor"))


def prueba_el_admin_administra_esquema_y_usuarios(nodo: int) -> None:
    with _cliente("centinela_admin", nodo) as admin:
        admin[BASE]["prueba_roles"].create_index("valor", name="idx_valor")
        admin[BASE].command({"collMod": "prueba_roles", "validator": {"valor": {"$exists": True}}})
        admin[BASE].command("createUser", "efimero", pwd="x", roles=[{"role": "read", "db": BASE}])
        admin[BASE].command("dropUser", "efimero")
        admin[BASE].create_collection("hecha_por_el_admin")
        admin[BASE].drop_collection("hecha_por_el_admin")
        # el admin tampoco sale de su base
        _denegado(lambda: admin["otra_base"]["cosas"].insert_one({"x": 1}))


def prueba_el_usuario_de_respaldo_lee_antifraude_y_solo_escribe_su_base_temporal(
    nodo: int,
) -> None:
    """D-74: lo que `mongodump` y la verificacion (`mongorestore` a una base temporal) piden,
    y nada mas. No escribe en antifraude, no la borra y no sale de sus dos bases."""
    temporal = f"{BASE}_verificacion"
    with _cliente("centinela_respaldo", nodo) as respaldo:
        assert respaldo[BASE]["prueba_roles"].find_one({"_id": "base"}) is not None
        _denegado(lambda: respaldo[BASE]["prueba_roles"].insert_one({"_id": "x"}))
        _denegado(lambda: respaldo[BASE]["prueba_roles"].delete_one({"_id": "base"}))
        _denegado(lambda: respaldo[BASE]["prueba_roles"].create_index("valor"))
        _denegado(lambda: respaldo.drop_database(BASE))
        # la base temporal si es suya: crea, restaura y borra
        respaldo[temporal]["coleccion"].insert_one({"_id": 1})
        respaldo[temporal].create_collection("otra")
        respaldo.drop_database(temporal)
        assert respaldo[temporal].list_collection_names() == []
        _denegado(lambda: respaldo["otra_base"]["cosas"].insert_one({"x": 1}))
    # y la app, que NO es la del respaldo, no puede tocar esa base
    with _cliente("centinela_app", nodo) as app:
        _denegado(lambda: app[temporal]["coleccion"].insert_one({"_id": 1}))


def prueba_el_admin_puede_borrar_la_base(nodo: int) -> None:
    """Va al final: es lo que ni la app ni el analista pueden hacer."""
    with _cliente("centinela_admin", nodo) as admin:
        admin.drop_database(BASE)
        assert "prueba_roles" not in admin[BASE].list_collection_names()
