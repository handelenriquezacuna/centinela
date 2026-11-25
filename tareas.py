#!/usr/bin/env python3
"""Lanzador de tareas de Centinela.

Solo biblioteca estandar, a proposito: `argparse` y `subprocess`. No es make, no es
just, no es un script de shell. La razon es concreta: el equipo trabaja en macOS y en
Windows, y make no viene en Windows mientras que `python tareas.py` corre igual en los
dos. Un archivo .sh habria excluido a la mitad del equipo.

Uso:

    python tareas.py arriba          levanta el replica set, lo inicializa y lo asegura
    python tareas.py asegurar        activa la autenticacion en una base existente (H-25)
    python tareas.py abajo           apaga el replica set
    python tareas.py estado          muestra el estado del replica set
    python tareas.py instalar        sincroniza dependencias con uv
    python tareas.py dev             corre la API en el host, con recarga (construye la SPA si falta)
    python tareas.py dev --front     lo mismo, mas Vite con recarga de la SPA en :8173 (T-61)
    python tareas.py front           instala (npm ci) y construye la SPA en app/estaticos/spa (T-61)
    python tareas.py datos-demo      carga el conjunto de demo
    python tareas.py demo-reiniciar --si  deja la base en el estado de la defensa
    python tareas.py migrar          corre las migraciones de esquema (H-26)
    python tareas.py validadores     actualiza los validadores sin borrar datos (T-24)
    python tareas.py generar [...]   corre el generador de datos (T-28), con sus banderas
    python tareas.py usuarios [...]  administra los usuarios de la app (T-41)
    python tareas.py indicadores [...] recalcula indicadores_diarios (H-22)
    python tareas.py rasgos [...]    recalcula los rasgos de grafo de R-11 (T-63)
    python tareas.py demo-cadena-viva [--disparar]  cadena de mulas con fecha de ahora para ensenar R-11 (D-68)
    python tareas.py cargar --archivo x.jsonl  carga transacciones desde JSONL, en lotes (T-57)
    python tareas.py pruebas         corre las pruebas
    python tareas.py e2e [...]             recorridos con navegador real (Chromium) contra la app (T-58)
    python tareas.py resiliencia     failover de D1 con un replica set de 3 nodos en Docker (T-52)
    python tareas.py verificar       instala, prueba y reporta

Con `--dry-run` cualquier tarea imprime lo que haria sin hacerlo.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

RAIZ = Path(__file__).resolve().parent
COMPOSE = RAIZ / "infra" / "docker-compose.yml"

# El replica set y su script de inicializacion ya existian antes de H-00A. Este
# lanzador los reusa, no los redefine.
SCRIPT_INIT = RAIZ / "scripts" / "init-replicaset.js"

# H-25. Usuarios de base de datos, secretos locales y autenticacion.
SCRIPT_USUARIOS = RAIZ / "scripts" / "semilla" / "04-usuarios.js"
ARCHIVO_ENV = RAIZ / ".env"
DIRECTORIO_SECRETOS = RAIZ / "infra" / "secretos"
KEYFILE = DIRECTORIO_SECRETOS / "keyfile"
BASE_DATOS = "antifraude"
VARIABLE_URI = "CENTINELA_MONGO__URI"
VARIABLE_SIN_AUTH = "CENTINELA_SIN_AUTH"
VARIABLE_CLAVE_SESION = "CENTINELA_SESION__CLAVE"

# usuario de base de datos -> variable de entorno con su contrasena
USUARIOS = {
    "centinela_app": "MONGO_CLAVE_APP",
    "centinela_analista": "MONGO_CLAVE_ANALISTA",
    "centinela_admin": "MONGO_CLAVE_ADMIN",
    "centinela_pruebas": "MONGO_CLAVE_PRUEBAS",
}

# La siembra de estructura y catalogos, EN ESTE ORDEN y no en otro: 01 hace drop de
# las colecciones para recrearlas con su validador, asi que correrlo despues del 03 se
# lleva el catalogo de reglas que el 03 acaba de escribir. Es un error que ya ocurrio
# en vivo, y el orden es la unica proteccion.
SCRIPTS_SEMILLA = (
    "01-colecciones.js",  # colecciones y validadores $jsonSchema (H-01)
    "02-indices.js",      # indices con nombre (H-06)
    "03-catalogos.js",    # reglas de deteccion y politica de severidad (H-07)
    "05-vistas.js",       # vistas de consulta guardadas (H-24); 04 es de `asegurar`
)


# Migraciones de esquema (H-26). Se corren TODAS, en orden de nombre: cada una es
# idempotente (solo toca documentos de la version que convierte), asi que correrlas de
# nuevo no cambia nada.
DIRECTORIO_MIGRACIONES = RAIZ / "scripts" / "migraciones"

# D-15. La historia de versiones de una regla (H-16) vive DENTRO del documento de la
# regla, y 01-colecciones.js hace drop + create de `reglas_deteccion`.
AVISO_HISTORIA_REGLAS = (
    "AVISO (D-15): re-sembrar BORRA la historia de versiones de las reglas.\n"
    "  01-colecciones.js recrea reglas_deteccion y 03-catalogos.js la deja en version 1,\n"
    "  sin versiones_anteriores. Las ediciones hechas desde la API (H-16) se pierden, y\n"
    "  una alerta evaluada con una version editada ya no se podra explicar con ella\n"
    "  (la migracion 001 la marca como 'no disponible: regla editada').\n"
)


class Fallo(Exception):
    """Error esperado de una tarea. Se reporta sin rastro de pila."""


def _buscar(programa: str) -> str:
    ruta = shutil.which(programa)
    if ruta is None:
        raise Fallo(
            f"no se encontro '{programa}' en el PATH.\n"
            f"  uv:     https://docs.astral.sh/uv/getting-started/installation/\n"
            f"  docker: Docker Desktop\n"
            f"  npm:    Node.js, la version de frontend/.nvmrc"
        )
    return ruta


def correr(
    orden: list[str],
    *,
    dry_run: bool = False,
    permitir_fallo: bool = False,
    env: dict[str, str] | None = None,
) -> int:
    """Ejecuta un comando mostrandolo antes. Sin shell: los argumentos van tal cual.

    Lo que se muestra nunca lleva contrasenas: las URIs con usuario se pasan a mongosh
    como argumento, asi que se ocultan en la linea impresa.
    """
    print("$ " + " ".join(_sin_secretos(parte) for parte in orden))
    if dry_run:
        return 0

    resultado = subprocess.run(orden, cwd=RAIZ, check=False, env=env)
    if resultado.returncode != 0 and not permitir_fallo:
        raise Fallo(f"el comando termino con codigo {resultado.returncode}")
    return resultado.returncode


def compose(*argumentos: str) -> list[str]:
    return [_buscar("docker"), "compose", "-f", str(COMPOSE), *argumentos]


def uv(*argumentos: str) -> list[str]:
    return [_buscar("uv"), *argumentos]


def _sin_secretos(texto: str) -> str:
    """Oculta `usuario:clave@` de una URI para imprimirla."""
    if "://" not in texto or "@" not in texto:
        return texto
    esquema, resto = texto.split("://", 1)
    if "@" not in resto.split("/", 1)[0]:
        return texto
    return f"{esquema}://***@{resto.split('@', 1)[1]}"


def cargar_entorno() -> None:
    """Carga `.env` en el entorno del proceso, sin pisar lo que ya este definido.

    El entorno real le gana al archivo (misma regla que la configuracion). Los valores
    vacios se ignoran: un `.env` copiado del molde no debe anular nada.
    """
    if not ARCHIVO_ENV.is_file():
        return
    for linea in ARCHIVO_ENV.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        nombre, valor = linea.split("=", 1)
        if valor.strip():
            os.environ.setdefault(nombre.strip(), valor.strip())


_URI_BASE: list[str] = []


def uri_base() -> str:
    """URI del replica set SIN credenciales, leida de la configuracion y no escrita aqui.

    `app/nucleo/config.py` es el unico lugar que lee el YAML, asi que el lanzador se
    la pregunta en vez de duplicarla. Se pregunta sin `CENTINELA_MONGO__URI` en el
    entorno: esa variable lleva el usuario (H-25) y aqui se quiere la URI del YAML.
    `uv run` sincroniza el entorno solo si hace falta.
    """
    if _URI_BASE:
        return _URI_BASE[0]
    externa = os.environ.get(VARIABLE_URI, "").strip()
    if externa:
        # T-53: quien exporta la variable apunta a OTRA base (CI, nodo de prueba).
        # Los pasos de mongosh deben ir a esa base y nunca al YAML: antes se tomaba
        # el host del YAML y un agente recreo las colecciones de la base viva.
        _URI_BASE.append(_sin_credenciales(externa))
        return _URI_BASE[0]
    entorno = {k: v for k, v in os.environ.items() if k != VARIABLE_URI}
    uri = _salida(
        uv(
            "run",
            "python",
            "-c",
            "from app.nucleo.config import obtener_ajustes;"
            "print(obtener_ajustes().mongo.uri)",
        ),
        env=entorno,
    )
    if not uri:
        raise Fallo(
            "no se pudo leer mongo.uri de la configuracion.\n"
            "Probar primero: python tareas.py instalar"
        )
    _URI_BASE.append(uri.splitlines()[-1].strip())
    return _URI_BASE[0]


def _sin_credenciales(uri: str) -> str:
    """Quita `usuario:clave@` de una URI; las credenciales se agregan aparte."""
    partes = urlsplit(uri)
    netloc = partes.netloc.rsplit("@", 1)[-1]
    return urlunsplit((partes.scheme, netloc, partes.path, partes.query, partes.fragment))


def _con_usuario(uri: str, usuario: str | None) -> str:
    """Agrega `usuario:clave@` y `authSource` a una URI. Sin clave, la deja igual.

    Sin clave en el entorno se trabaja como antes de H-25 (base sin autenticacion),
    que es lo que hace falta para una base vieja o un nodo de CI.
    """
    clave = os.environ.get(USUARIOS[usuario], "") if usuario else ""
    if not clave:
        return uri
    esquema, resto = uri.split("://", 1)
    separador = "&" if "?" in resto else "?"
    return (
        f"{esquema}://{quote(usuario, safe='')}:{quote(clave, safe='')}@{resto}"
        f"{separador}authSource={BASE_DATOS}"
    )


def uri_usuario(usuario: str | None = None) -> str:
    """URI del replica set, con las credenciales de `usuario` si estan en el entorno."""
    externa = os.environ.get(VARIABLE_URI, "").strip()
    if externa and "@" in urlsplit(externa).netloc:
        return externa  # ya trae su usuario: se usa tal cual
    return _con_usuario(uri_base(), usuario)


def uri_directa(usuario: str | None = None) -> str:
    """URI de conexion directa al primer nodo, sin descubrimiento de replica set.

    Hace falta para `rs.initiate()`: antes de iniciar el conjunto no hay replica set
    que descubrir, asi que una URI con `replicaSet=rsfraude` falla con
    `MongoServerSelectionError` y parece que el cluster no levanto. Tambien para las
    comprobaciones de acceso y de conteo, que no deben depender de la eleccion.

    El host sale de la URI configurada; no se escribe aqui.
    """
    primer_nodo = urlsplit(uri_base()).netloc.split(",")[0]
    return _con_usuario(
        f"mongodb://{primer_nodo}/?directConnection=true&serverSelectionTimeoutMS=8000",
        usuario,
    )


def mongosh_directo(*argumentos: str) -> list[str]:
    """`mongosh` contra un solo nodo y sin credenciales. Solo para inicializar."""
    return [_buscar("mongosh"), uri_directa(), "--quiet", *argumentos]


def entorno_app(*, usuario: str = "centinela_app") -> dict[str, str]:
    """Entorno para los procesos de Python: la URI con usuario va por variable.

    Si el entorno ya trae `CENTINELA_MONGO__URI` (CI, o alguien que apunta a otra
    base), se respeta. Sin claves, no se agrega nada: base sin autenticacion.
    """
    entorno = dict(os.environ)
    if VARIABLE_URI not in entorno:
        uri = uri_usuario(usuario)
        if uri != uri_base():
            entorno[VARIABLE_URI] = uri
    return entorno


def entorno_compose(*, sin_auth: bool = False) -> dict[str, str]:
    """Entorno de `docker compose`: el keyfile se agrega salvo en la fase sin auth."""
    entorno = entorno_app()
    entorno.pop(VARIABLE_SIN_AUTH, None)
    if sin_auth:
        entorno[VARIABLE_SIN_AUTH] = "1"
    return entorno


def mongosh(*argumentos: str, usuario: str | None = None) -> list[str]:
    """`mongosh` DESDE EL HOST, con la URI completa del replica set.

    Dos razones para no usar `docker compose exec`, y las dos ya nos costaron tiempo:

    1. `mongosh --port 27018` habla con un nodo suelto, y mongo1 no siempre es el
       primario: despues de una eleccion puede contestar "not primary". Con la URI
       completa, el driver descubre el primario solo.
    2. El bind mount de /scripts apunta a la carpeta del host de quien recreo los
       contenedores de ultimo. Con varios checkouts del repo, `--file /scripts/x.js`
       lee el archivo de otra persona. Corriendo desde el host con ruta absoluta del
       host, el archivo es siempre el propio.
    """
    return [_buscar("mongosh"), uri_usuario(usuario), "--quiet", *argumentos]


def _salida(orden: list[str], env: dict[str, str] | None = None) -> str:
    """Ejecuta y devuelve la salida. Cadena vacia si el comando falla."""
    try:
        resultado = subprocess.run(
            orden, cwd=RAIZ, capture_output=True, text=True, timeout=60, check=False, env=env
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return resultado.stdout.strip() if resultado.returncode == 0 else ""


def esperar_primario(segundos: int = 60, dry_run: bool = False) -> bool:
    """Espera a que la eleccion termine y haya un primario.

    Sin esta espera, `arriba` reporta tres SECONDARY y parece que algo salio mal: la
    eleccion tarda entre 5 y 15 segundos y el comando terminaba antes. Es la causa
    de la fila "el primario no aparece" de la tabla de problemas de
    docs/05-infraestructura.md.
    """
    if dry_run:
        return True

    # `hello` no pide privilegios: sirve con la autenticacion encendida y sin usuario.
    consulta = "db.hello().isWritablePrimary"
    orden = mongosh("--eval", consulta)  # la URI se resuelve una sola vez
    limite = time.monotonic() + segundos

    while time.monotonic() < limite:
        if _salida(orden) == "true":
            return True
        time.sleep(2)

    return False


# ---------------------------------------------------------------------------
# Secretos y autenticacion (H-25)
# ---------------------------------------------------------------------------


def asegurar_secretos(dry_run: bool = False) -> None:
    """Crea `.env` y el keyfile la primera vez; no toca lo que ya existe.

    Las contrasenas son aleatorias (`secrets.token_urlsafe`) y no se versionan: .env y
    infra/secretos/ estan en .gitignore. El keyfile es base64 (lo que mongod exige) y
    queda con permisos 400.
    """
    if not ARCHIVO_ENV.is_file():
        print("Creando .env con contrasenas aleatorias (no se versiona)...")
        if not dry_run:
            lineas = [
                "# Generado por `python tareas.py arriba`. No se versiona. Para rotar una",
                "# contrasena: cambiarla aqui y correr `python tareas.py asegurar`.",
            ]
            lineas += [f"{variable}={secrets.token_urlsafe(24)}" for variable in USUARIOS.values()]
            # Clave de firma de la sesion de la app (T-41): minimo 32 caracteres.
            lineas.append(f"{VARIABLE_CLAVE_SESION}={secrets.token_urlsafe(48)}")
            ARCHIVO_ENV.write_text("\n".join(lineas) + "\n", encoding="utf-8")
            with contextlib.suppress(OSError):
                ARCHIVO_ENV.chmod(0o600)
    elif VARIABLE_CLAVE_SESION not in ARCHIVO_ENV.read_text(encoding="utf-8"):
        print("Agregando la clave de sesion a .env...")
        if not dry_run:
            with ARCHIVO_ENV.open("a", encoding="utf-8") as archivo:
                archivo.write(f"{VARIABLE_CLAVE_SESION}={secrets.token_urlsafe(48)}\n")
    if not KEYFILE.is_file():
        print("Creando el keyfile del replica set (infra/secretos/keyfile, permisos 400)...")
        if not dry_run:
            DIRECTORIO_SECRETOS.mkdir(parents=True, exist_ok=True)
            contenido = base64.b64encode(secrets.token_bytes(756)).decode("ascii")
            descriptor = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
            with os.fdopen(descriptor, "w", encoding="ascii") as archivo:
                archivo.write(contenido + "\n")
    if not dry_run:
        cargar_entorno()


def entra(usuario: str) -> bool:
    """True si `usuario` consigue autenticarse y listar colecciones de antifraude.

    Falla cuando la base no tiene usuarios, cuando no tiene la autenticacion encendida
    (un nodo sin auth rechaza credenciales que no conoce) o cuando no responde.
    """
    if not os.environ.get(USUARIOS[usuario]):
        return False
    orden = [
        _buscar("mongosh"),
        uri_directa(usuario),
        "--quiet",
        "--eval",
        f'db.getSiblingDB("{BASE_DATOS}").getCollectionNames().length',
    ]
    try:
        resultado = subprocess.run(
            orden, cwd=RAIZ, capture_output=True, text=True, timeout=60, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return resultado.returncode == 0


def esperar_entrada(usuario: str, segundos: int = 90) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if entra(usuario):
            return True
        time.sleep(3)
    return False


CONTEO_JS = (
    f'const d = db.getSiblingDB("{BASE_DATOS}");'
    "JSON.stringify(Object.fromEntries(d.getCollectionNames()"
    '.filter(n => !n.startsWith("system.")).sort()'
    ".map(n => [n, d.getCollection(n).countDocuments({})])))"
)


def contar_documentos(usuario: str | None) -> dict[str, int] | None:
    """Documentos por coleccion de antifraude, o None si no se pudo contar."""
    texto = _salida([_buscar("mongosh"), uri_directa(usuario), "--quiet", "--eval", CONTEO_JS])
    try:
        return json.loads(texto.splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _asegurar(args: argparse.Namespace) -> None:
    """Activa la autenticacion sobre la base que haya, sin perder documentos.

    1. Cuenta los documentos (con la auth apagada).
    2. Levanta los nodos SIN keyfile (fase de preparacion; los puertos siguen en
       127.0.0.1), inicializa el replica set si hace falta y crea roles y usuarios con
       `04-usuarios.js`.
    3. Recrea los nodos CON keyfile: desde aqui todo exige usuario y contrasena. Los
       volumenes no se tocan, asi que los datos son los mismos.
    4. Comprueba el acceso de los tres usuarios y que el conteo coincide.

    Es idempotente. Si algo falla a medias, volver a correr el comando termina el trabajo.
    """
    dry = args.dry_run
    asegurar_secretos(dry)

    print("\n[1/4] Conteo previo de documentos...")
    antes = None if dry else contar_documentos(None)
    if antes is None and not dry:
        print("  sin datos previos (base nueva o nodos apagados)")
    elif antes is not None:
        print(f"  {sum(antes.values())} documentos en {len(antes)} colecciones")

    print("\n[2/4] Nodos sin keyfile, replica set y usuarios...")
    correr(
        compose("up", "-d", "--wait", "--force-recreate"),
        dry_run=dry,
        env=entorno_compose(sin_auth=True),
    )
    correr(mongosh_directo("--file", str(SCRIPT_INIT)), dry_run=dry)
    if not dry and not esperar_primario():
        raise Fallo("no aparecio un primario. Ver: python tareas.py estado")
    claves = {nombre: os.environ.get(variable, "") for nombre, variable in USUARIOS.items()}
    if not dry and not all(claves.values()):
        raise Fallo("faltan contrasenas en .env; borrar .env y correr de nuevo asegurar")
    correr(
        [_buscar("mongosh"), uri_usuario(), "--quiet", "--file", str(SCRIPT_USUARIOS)],
        dry_run=dry,
    )

    print("\n[3/4] Nodos con keyfile: la autenticacion queda encendida...")
    orden = compose("up", "-d", "--wait", "--force-recreate")
    # Un reintento: en Docker Desktop el primer montaje de un keyfile recien creado a
    # veces no lo ve (`install: cannot open /secretos/keyfile`) y el nodo sale con 1.
    if correr(orden, dry_run=dry, env=entorno_compose(), permitir_fallo=True) != 0:
        print("El nodo no arranco con el keyfile; un reintento...")
        time.sleep(5)
        correr(orden, dry_run=dry, env=entorno_compose())
    if dry:
        return

    print("\n[4/4] Comprobacion...")
    for usuario in ("centinela_admin", "centinela_app", "centinela_analista"):
        if not esperar_entrada(usuario):
            raise Fallo(f"{usuario} no consigue entrar. Ver el log: docker logs fraude-mongo1")
        print(f"  {usuario}: entra")
    if not esperar_primario():
        raise Fallo("no aparecio un primario tras activar la autenticacion")
    despues = contar_documentos("centinela_admin")
    if antes is not None and despues != antes:
        raise Fallo(f"el conteo cambio.\n  antes:   {antes}\n  despues: {despues}")
    if despues is not None:
        print(f"  {sum(despues.values())} documentos en {len(despues)} colecciones"
              + ("; igual que antes" if antes is not None else ""))
    print("\nListo. Desde ahora la base exige usuario. URI de Compass: docs/05-infraestructura.md")


# ---------------------------------------------------------------------------
# Tareas
# ---------------------------------------------------------------------------


def tarea_arriba(args: argparse.Namespace) -> None:
    """Levanta los tres nodos e inicializa el replica set.

    Los dos pasos van juntos porque separarlos es la causa numero uno de
    `NotYetInitialized`: alguien levanta los contenedores, se olvida del segundo
    comando y pasa media hora buscando por que fallan los change streams.

    `init-replicaset.js` es idempotente: correrlo de nuevo no hace nada.

    Con `--con-app` se agrega un tercer paso al final: levantar tambien el servicio
    `app` (la API en un contenedor, modalidad de arranque B). Va DESPUES de esperar
    el primario y no junto con los tres nodos, a proposito: la API arrancaria de
    todos modos aunque el replica set siguiera eligiendo primario (el cliente de
    Mongo conecta perezoso, ver `app/main.py`), pero levantarla despues da un primer
    `/salud` limpio en vez de uno que reporta el replica set todavia formandose.
    """
    asegurar_secretos(args.dry_run)
    correr(compose("up", "-d", "--wait"), dry_run=args.dry_run, env=entorno_compose())

    # H-25. Si no hay un usuario valido, la base es nueva o es de antes de la
    # autenticacion: `_asegurar` la inicializa y le crea los usuarios (idempotente).
    # Si ya los hay, el replica set esta inicializado y solo se espera el primario.
    if args.dry_run or not entra("centinela_admin"):
        print("\nLa base no tiene usuarios: se asegura (replica set, usuarios, keyfile)...")
        _asegurar(args)

    print("\nEsperando la eleccion del primario...")
    if not esperar_primario(dry_run=args.dry_run):
        raise Fallo(
            "no aparecio un primario. Ver el estado con: python tareas.py estado\n"
            "Si los contenedores se caen al arrancar, suele ser poca memoria "
            "asignada a Docker."
        )

    print("\nEstado:")
    tarea_estado(args)

    if not args.con_app:
        asegurar_spa(args)
        print(
            "\nListo. Siguiente paso:\n"
            "  python tareas.py datos-demo\n"
            "  python tareas.py dev"
        )
        return

    print("\nLevantando el servicio app (la API en un contenedor)...")
    correr(
        compose("--profile", "app", "up", "-d", "--build", "--wait", "app"),
        dry_run=args.dry_run,
        env=entorno_compose(),
    )
    print(
        "\nListo, con la API dentro de Compose. Siguiente paso:\n"
        "  python tareas.py datos-demo\n"
        "  curl http://localhost:8000/salud"
    )


def tarea_abajo(args: argparse.Namespace) -> None:
    """Apaga los contenedores. Los datos sobreviven en los volumenes.

    Lleva `--profile app` siempre, se haya usado `--con-app` o no: un servicio con
    perfil que Compose no ve activo en ESTA invocacion no cuenta como "definido" para
    `down`, y sin la bandera el contenedor de la API sobrevive, huerfano, sin la red
    de mongo1 que se acaba de apagar. Pedir el perfil no hace nada si `app` no estaba
    arriba; si estaba, es la unica forma de que este comando lo apague tambien.
    """
    orden = compose("--profile", "app", "down")
    if args.borrar_datos:
        orden.append("-v")
        print("Se borran los volumenes: el replica set habra que inicializarlo de nuevo.")
    correr(orden, dry_run=args.dry_run, env=entorno_compose())


def tarea_asegurar(args: argparse.Namespace) -> None:
    """Activa la autenticacion (usuarios y keyfile) sobre una base existente (H-25).

    Para la base que corre sin auth y con datos: crea los usuarios, genera el keyfile y
    reinicia con autenticacion, sin tocar los volumenes. Cuenta los documentos antes y
    despues. Vuelta atras: docs/05-infraestructura.md. Idempotente: si ya esta
    asegurada, no hace nada.
    """
    asegurar_secretos(args.dry_run)
    if not args.dry_run and entra("centinela_admin"):
        print("La base ya exige autenticacion y centinela_admin entra. Nada que hacer.")
        print("Para rotar contrasenas: cambiarlas en .env y correr `arriba` otra vez.")
        return
    _asegurar(args)


def tarea_estado(args: argparse.Namespace) -> None:
    """Un primario y dos secundarios es lo que se espera ver.

    Usa `hello`, que no pide privilegios: ningun usuario de Centinela es de clusterMonitor
    (rs.status() quedaria prohibido con la autenticacion encendida).
    """
    correr(
        mongosh(
            "--eval",
            "const h = db.hello(); h.hosts.map(m => m + ' -> ' + "
            "(m === h.primary ? 'PRIMARY' : 'SECONDARY')).join('\\n')",
        ),
        dry_run=args.dry_run,
        permitir_fallo=True,
    )


def tarea_instalar(args: argparse.Namespace) -> None:
    """Crea el entorno con el interprete y las versiones exactas del lock."""
    correr(uv("sync"), dry_run=args.dry_run)


FRONTEND = RAIZ / "frontend"
PUERTO_VITE = 8173


def npm(*argumentos: str) -> list[str]:
    """`npm` sobre `frontend/` sin cambiar de carpeta (`--prefix`)."""
    return [_buscar("npm"), "--prefix", str(FRONTEND), *argumentos]


def tarea_front(args: argparse.Namespace) -> None:
    """Instala las dependencias exactas del lock (npm ci) y construye la SPA (T-61).

    El build queda en app/estaticos/spa/ (ignorada por git) y lo sirve FastAPI cuando
    `portal.interfaz` es `spa`. Es lo mismo que corre el CI antes de las e2e.
    """
    correr(npm("ci"), dry_run=args.dry_run)
    correr(npm("run", "build"), dry_run=args.dry_run)


INDICE_SPA = RAIZ / "app" / "estaticos" / "spa" / "index.html"
# Lo que entra al build: si algo de esto es mas nuevo que el index, el build esta viejo.
FUENTES_SPA = ("src", "index.html", "package.json", "package-lock.json", "vite.config.ts")


def _spa_desactualizada() -> bool:
    """True si alguna fuente de frontend/ es mas nueva que el build (solo fechas, sin red)."""
    construido = INDICE_SPA.stat().st_mtime
    for nombre in FUENTES_SPA:
        ruta = FRONTEND / nombre
        archivos = ruta.rglob("*") if ruta.is_dir() else [ruta]
        if any(a.is_file() and a.stat().st_mtime > construido for a in archivos):
            return True
    return False


def _interfaz_portal() -> str:
    """`portal.interfaz` de la configuracion; sin el entorno de uv, la del entorno o `spa`."""
    try:
        from app.nucleo.config import Ajustes  # import tardio: no es stdlib
    except ImportError:
        return os.environ.get("CENTINELA_PORTAL__INTERFAZ", "spa")
    return Ajustes().portal.interfaz


def asegurar_spa(args: argparse.Namespace) -> None:
    """Con `portal.interfaz: spa` (la de por omision, T-67), deja la SPA construida.

    Si falta el build lo construye (npm ci + build, lo mismo que `front`); si no hay npm,
    avisa como seguir sin cortar el arranque. Si el build es mas viejo que las fuentes,
    solo avisa: reconstruir es `python tareas.py front`. Con `htmx` no hace nada. Es todo
    en el host y antes de arrancar: en ejecucion la app no usa Node ni la red.
    """
    if _interfaz_portal() != "spa":
        return
    if INDICE_SPA.is_file():
        if _spa_desactualizada():
            print(
                "AVISO: el build de la SPA es mas viejo que frontend/. Para ver los cambios:\n"
                "  python tareas.py front"
            )
        return
    if shutil.which("npm") is None:
        print(
            "AVISO: falta el build de la SPA (app/estaticos/spa/) y npm no esta en el PATH.\n"
            "  Instalar Node (la version de frontend/.nvmrc) y correr: python tareas.py front\n"
            "  O usar el respaldo HTMX: CENTINELA_PORTAL__INTERFAZ=htmx python tareas.py dev"
        )
        return
    print("\nFalta el build de la SPA: se construye una vez (npm ci + build)...")
    tarea_front(args)


def tarea_dev(args: argparse.Namespace) -> None:
    """Modalidad de arranque A: Mongo en Compose, Python en el host con recarga.

    Un solo worker a proposito: la deteccion tiene que estar activa exactamente una
    vez. Dos workers escuchando el mismo change stream crean la alerta dos veces.

    Con `--front` levanta ademas Vite en http://127.0.0.1:8173 con recarga en caliente de
    la SPA; Vite manda la API, el SSE y los estaticos a esta misma API (proxy, mismo
    origen para el navegador, la cookie viaja sola). Al salir se apagan los dos.
    """
    from app.nucleo.config import obtener_ajustes  # import tardio: no es stdlib

    ajustes = obtener_ajustes()
    asegurar_spa(args)
    orden = uv(
        "run",
        "uvicorn",
        "app.main:app",
        "--host",
        ajustes.servidor.host,
        "--port",
        str(ajustes.servidor.puerto),
    )
    if ajustes.servidor.recarga:
        orden.append("--reload")

    print(
        f"API en http://{ajustes.servidor.host}:{ajustes.servidor.puerto}\n"
        f"  documentacion: http://{ajustes.servidor.host}:{ajustes.servidor.puerto}/docs\n"
        f"  salud:         http://{ajustes.servidor.host}:{ajustes.servidor.puerto}/salud\n"
    )
    if not args.front:
        correr(orden, dry_run=args.dry_run, env=entorno_app())
        return

    vite = npm("run", "dev")
    entorno_vite = {
        **os.environ,
        "CENTINELA_API": f"http://{ajustes.servidor.host}:{ajustes.servidor.puerto}",
    }
    print(f"SPA (Vite) en http://127.0.0.1:{PUERTO_VITE}\n$ " + " ".join(vite))
    if args.dry_run:
        correr(orden, dry_run=True)
        return
    proceso_vite = subprocess.Popen(vite, cwd=RAIZ, env=entorno_vite)
    try:
        correr(orden, env=entorno_app())
    finally:
        proceso_vite.terminate()
        try:
            proceso_vite.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proceso_vite.kill()


def tarea_datos_demo(args: argparse.Namespace) -> None:
    """Siembra estructura y catalogos, y carga el conjunto de demo.

    Tres pasos, en orden:

    1. `scripts/semilla/01-colecciones.js` y `02-indices.js`: colecciones con sus
       validadores e indices.
    2. `scripts/semilla/03-catalogos.js`: el catalogo de reglas y la politica de
       severidad. El catalogo NO esta en el cargador de Python a proposito: definirlo
       en dos lugares es garantizar que se separen.
    3. Los documentos de dominio del caso documentado (Maria Rodriguez, TXN-001).

    Los scripts de semilla son de H-01, H-06 y H-07. Si todavia no estan en este
    checkout, el paso 1 y 2 se omiten con aviso y se carga solo el dominio.
    """
    directorio = RAIZ / "scripts" / "semilla"
    presentes = [directorio / nombre for nombre in SCRIPTS_SEMILLA]
    faltantes = [ruta.name for ruta in presentes if not ruta.is_file()]

    print(AVISO_HISTORIA_REGLAS)

    if faltantes:
        print(
            "AVISO: no estan en este checkout los scripts de semilla "
            f"{', '.join(faltantes)}.\n"
            "  Llegan con las ramas de H-01, H-06 y H-07. Se carga solo el dominio;\n"
            "  el catalogo de reglas y la politica de severidad quedan sin sembrar.\n"
        )
    else:
        for ruta in presentes:
            print(f"Sembrando {ruta.name}...")
            correr(mongosh("--file", str(ruta), usuario="centinela_admin"), dry_run=args.dry_run)

    print("Cargando los documentos de dominio del demo...")
    correr(
        uv("run", "python", "-m", "pruebas.dobles.datos_demo"),
        dry_run=args.dry_run,
        env=entorno_app(),
    )

# Variable por la que viajan los agentes con credencial entre la lectura y la
# restauracion del reinicio: por el entorno y no por la linea de comandos, para que los
# hashes no se impriman ni queden en el listado de procesos.
VARIABLE_AGENTES = "CENTINELA_REINICIO_AGENTES"

LEER_AGENTES_JS = (
    f'const d = db.getSiblingDB("{BASE_DATOS}");'
    'print(EJSON.stringify(d.agentes.find({credencial: {$exists: true}}).toArray(),'
    " {relaxed: false}))"
)
RESTAURAR_AGENTES_JS = (
    f'const d = db.getSiblingDB("{BASE_DATOS}");'
    f"const agentes = EJSON.parse(process.env.{VARIABLE_AGENTES});"
    "agentes.forEach(a => d.agentes.replaceOne({_id: a._id}, a, {upsert: true}));"
    "print(agentes.length + ' agentes con credencial restaurados')"
)


def tarea_demo_reiniciar(args: argparse.Namespace) -> None:
    """Deja la base en el estado exacto de la defensa (borra datos; pide --si).

    Siembra 01 a 03 y 05, carga el conjunto de demo y recalcula `indicadores_diarios` del
    dia del demo. 01-colecciones.js hace drop de `agentes`, y con ella se irian las
    credenciales de login (H-25): se leen ANTES (los agentes que tienen `credencial`) y
    se restauran DESPUES de cargar el demo, que reemplaza a AGT-001 y AGT-002 por sus
    documentos sin credencial. Si la lectura falla, no se borra nada.

    Corre con el usuario administrador de `.env`, igual que `asegurar` y `datos-demo`.
    """
    if not args.si:
        raise Fallo(
            "demo-reiniciar BORRA los datos de la base antifraude y los vuelve a sembrar.\n"
            "  Se conservan las credenciales de login de los agentes.\n"
            "  Para seguir: python tareas.py demo-reiniciar --si"
        )
    dry = args.dry_run

    print("[1/4] Leyendo las credenciales de login de los agentes...")
    guardadas = "[]"
    if not dry:
        texto = _salida(mongosh("--eval", LEER_AGENTES_JS, usuario="centinela_admin"))
        guardadas = texto.splitlines()[-1] if texto else ""
        try:
            cantidad = len(json.loads(guardadas))
        except (ValueError, TypeError):
            raise Fallo(
                "no se pudieron leer los agentes (base apagada o sin acceso). "
                "No se borro nada."
            ) from None
        print(f"  {cantidad} agentes con credencial")

    print("\n[2/4] Sembrando 01 a 03 y 05, y cargando el demo...")
    tarea_datos_demo(args)

    print("\n[3/4] Restaurando las credenciales...")
    correr(
        mongosh("--eval", RESTAURAR_AGENTES_JS, usuario="centinela_admin"),
        dry_run=dry,
        env={**os.environ, VARIABLE_AGENTES: guardadas},
    )

    print("\n[4/4] Recalculando indicadores del dia del demo...")
    dia = "2026-09-24" if dry else _salida(
        uv("run", "python", "-c", "from pruebas.dobles.datos_demo import DIA;print(DIA)")
    ).splitlines()[-1]
    correr(
        uv("run", "python", "-m", "app.deteccion.indicadores", "--desde", dia, "--hasta", dia),
        dry_run=dry,
        env=entorno_app(),
    )
    print("\nListo. La base quedo como el dia de la defensa.")


def tarea_migrar(args: argparse.Namespace) -> None:
    """Corre las migraciones de esquema de scripts/migraciones, en orden.

    Por nombre, en orden: `001-alertas-v1-a-v2.js` (H-26) y
    `002-transacciones-sinpe-origen.js` (H-23). Cada script imprime cuantos documentos
    migro y cuantos ya estaban migrados; una segunda corrida migra cero. Se corre desde
    el host con la URI completa, igual que la siembra, por las mismas razones (ver
    `mongosh`).
    """
    scripts = sorted(DIRECTORIO_MIGRACIONES.glob("*.js"))
    if not scripts:
        raise Fallo(f"no hay migraciones en {DIRECTORIO_MIGRACIONES}")
    for ruta in scripts:
        print(f"Migrando con {ruta.name}...", flush=True)
        correr(mongosh("--file", str(ruta), usuario="centinela_admin"), dry_run=args.dry_run)


def tarea_validadores(args: argparse.Namespace) -> None:
    """Actualiza los validadores de la base sin borrar datos (T-24).

    Re-sembrar (`datos-demo`) es la otra forma de refrescar un validador viejo, y borra
    todo. Este script carga 01-colecciones.js contra una base temporal, copia los
    validadores resultantes a `antifraude` con collMod y descarta la temporal. Corre
    desde el host con la URI completa y la ruta absoluta del script, igual que la siembra.
    """
    directorio = RAIZ / "scripts" / "semilla"
    correr(
        mongosh(
            "--eval",
            f"var RUTA_COLECCIONES={json.dumps(str(directorio / '01-colecciones.js'))}",
            "--file",
            str(directorio / "actualizar-validadores.js"),
            usuario="centinela_admin",
        ),
        dry_run=args.dry_run,
    )


def tarea_generar(args: argparse.Namespace) -> None:
    """Corre el generador de datos (T-28); las banderas pasan tal cual.

    Ejemplo: python tareas.py generar --clientes 50 --transacciones 5000 --semilla 7
    (todas las banderas con `python tareas.py generar --help`).
    """
    correr(
        uv("run", "python", "-m", "app.generador", *args.argumentos),
        dry_run=args.dry_run,
        env=entorno_app(),
    )


def tarea_indicadores(args: argparse.Namespace) -> None:
    """Recalcula indicadores_diarios (H-22); sin banderas, el dia de hoy.

    Ejemplo: python tareas.py indicadores --desde 2026-11-01 --hasta 2026-11-22
    """
    correr(
        uv("run", "python", "-m", "app.deteccion.indicadores", *args.argumentos),
        dry_run=args.dry_run,
        env=entorno_app(),
    )


def tarea_rasgos(args: argparse.Namespace) -> None:
    """Recalcula los rasgos de grafo por cuenta que lee R-11 (T-63).

    Ejemplo: python tareas.py rasgos --horas 48 --ventana-minutos 15
    Sin banderas usa la ventana de la regla red_mulas y el horizonte del YAML. Se puede
    volver a correr: reemplaza el documento de cada cuenta. D3 hace lo mismo cada pocos
    segundos cuando R-11 esta activa.
    """
    correr(
        uv("run", "python", "-m", "app.deteccion.rasgos_grafo", *args.argumentos),
        dry_run=args.dry_run,
        env=entorno_app(),
    )


def tarea_demo_cadena_viva(args: argparse.Namespace) -> None:
    """Inserta la cadena de mulas con fecha de ahora y calcula el rasgo de R-11 (D-68).

    Ejemplo: python tareas.py demo-cadena-viva --disparar
    No borra nada y se puede repetir. Con --disparar agrega una transferencia hacia
    6033-9001 para que D1, si esta corriendo, levante la alerta con R-11. Respeta
    CENTINELA_MONGO__URI. Necesita el demo cargado (datos-demo).
    """
    correr(
        uv("run", "python", "-m", "app.deteccion.demo_cadena", *args.argumentos),
        dry_run=args.dry_run,
        env=entorno_app(),
    )


def tarea_cargar(args: argparse.Namespace) -> None:
    """Carga transacciones desde un archivo JSONL, en lotes y sin duplicar (T-57).

    Ejemplo: python tareas.py cargar --archivo transacciones.jsonl
    Reporta cargadas, repetidas y rechazadas con su numero de linea. Se puede volver a
    correr: lo ya cargado cuenta como repetido. Respeta CENTINELA_MONGO__URI.
    """
    correr(
        uv("run", "python", "-m", "app.carga", *args.argumentos),
        dry_run=args.dry_run,
        env=entorno_app(),
    )


def tarea_usuarios(args: argparse.Namespace) -> None:
    """Administra los usuarios de la app (T-41); las banderas pasan tal cual.

    Ejemplo: python tareas.py usuarios --help
    """
    correr(
        uv("run", "python", "-m", "app.seguridad.usuarios", *args.argumentos),
        dry_run=args.dry_run,
        env=entorno_app(),
    )


def tarea_pruebas(args: argparse.Namespace) -> None:
    """Corre las pruebas. Las que necesitan Mongo se saltan solas si no esta."""
    orden = uv("run", "pytest")

    if args.tipo != "todas":
        orden.append(f"pruebas/{args.tipo}")
    if args.sin_mongo:
        orden.extend(["-m", "not mongo"])
    if args.verboso:
        orden.append("-v")
    else:
        orden.append("-q")

    # Las pruebas escriben en bases propias (antifraude_pruebas*): con auth, entran con
    # centinela_pruebas, que solo administra esas bases y no toca antifraude.
    correr(orden, dry_run=args.dry_run, env=entorno_app(usuario="centinela_pruebas"))


def tarea_e2e(args: argparse.Namespace) -> None:
    """Pruebas extremo a extremo con navegador real: lo mismo que corre el CI (T-58).

    Las banderas pasan tal cual a pytest, para verlas correr:
      python tareas.py e2e --headed --slowmo 700 pruebas/e2e/prueba_spa_sankey.py
      CENTINELA_E2E_TRAZA=siempre python tareas.py e2e -k detalle
    y despues: uv run playwright show-trace pruebas/e2e/trazas/<prueba>.zip
    (no usar --tracing de pytest-playwright: el conftest ya graba la traza)
    """
    # Primera vez en la maquina: `uv run playwright install chromium` (el CI usa
    # --with-deps). La base de la prueba es `antifraude_pruebas_e2e`: se siembra, se usa y
    # se borra; la de trabajo no se toca. Con auth, entra con centinela_pruebas.
    entorno = entorno_app(usuario="centinela_pruebas")
    entorno["CENTINELA_EXIGIR_MONGO"] = "1"
    correr(
        uv("run", "pytest", "-m", "e2e", "-rs", *args.argumentos),
        dry_run=args.dry_run,
        env=entorno,
    )


def tarea_resiliencia(args: argparse.Namespace) -> None:
    """Prueba de failover de D1: levanta 3 nodos propios en Docker, mata al primario (T-52)."""
    # No usa el cluster del compose ni la base de trabajo: levanta y baja sus propios
    # contenedores t52f-* en los puertos 27272 a 27274. Solo corre si se pide por nombre.
    correr(
        uv("run", "pytest", "-m", "resiliencia", "-s", "-q"),
        dry_run=args.dry_run,
    )


def tarea_verificar(args: argparse.Namespace) -> None:
    """Lo que se corre antes de pedir revision."""
    tarea_instalar(args)
    tarea_pruebas(args)


TAREAS = {
    "arriba": tarea_arriba,
    "asegurar": tarea_asegurar,
    "abajo": tarea_abajo,
    "estado": tarea_estado,
    "instalar": tarea_instalar,
    "dev": tarea_dev,
    "front": tarea_front,
    "datos-demo": tarea_datos_demo,
    "demo-reiniciar": tarea_demo_reiniciar,
    "migrar": tarea_migrar,
    "validadores": tarea_validadores,
    "generar": tarea_generar,
    "indicadores": tarea_indicadores,
    "rasgos": tarea_rasgos,
    "demo-cadena-viva": tarea_demo_cadena_viva,
    "cargar": tarea_cargar,
    "usuarios": tarea_usuarios,
    "pruebas": tarea_pruebas,
    "e2e": tarea_e2e,
    "resiliencia": tarea_resiliencia,
    "verificar": tarea_verificar,
}


def construir_analizador() -> argparse.ArgumentParser:
    analizador = argparse.ArgumentParser(
        prog="python tareas.py",
        description="Lanzador de tareas de Centinela.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analizador.add_argument(
        "--dry-run",
        action="store_true",
        help="imprime los comandos sin ejecutarlos",
    )

    subs = analizador.add_subparsers(dest="tarea", required=True, metavar="tarea")

    for nombre, funcion in TAREAS.items():
        documento = (funcion.__doc__ or "").strip().splitlines()[0]
        sub = subs.add_parser(nombre, help=documento, description=funcion.__doc__)
        sub.set_defaults(funcion=funcion)

        if nombre == "arriba":
            sub.add_argument(
                "--con-app",
                action="store_true",
                dest="con_app",
                help=(
                    "tambien levanta el servicio 'app' (la API en un contenedor, "
                    "modalidad de arranque B)"
                ),
            )
        if nombre == "dev":
            sub.add_argument(
                "--front",
                action="store_true",
                help="tambien levanta Vite con recarga de la SPA (proxy a esta API)",
            )
        if nombre == "abajo":
            sub.add_argument(
                "--borrar-datos",
                action="store_true",
                help="borra tambien los volumenes (empezar de cero)",
            )
        if nombre == "demo-reiniciar":
            sub.add_argument(
                "--si",
                action="store_true",
                help="confirma que se borran los datos y se vuelve a sembrar",
            )
        if nombre in {"pruebas", "verificar"}:
            sub.add_argument(
                "--tipo",
                choices=["contrato", "unitarias", "arquitectura", "todas"],
                default="todas",
                help="tipo de prueba a correr",
            )
            sub.add_argument(
                "--sin-mongo",
                action="store_true",
                help="omite las pruebas marcadas mongo",
            )
            sub.add_argument("-v", "--verboso", action="store_true")

    return analizador


def main(argumentos: list[str] | None = None) -> int:
    analizador = construir_analizador()
    argumentos = sys.argv[1:] if argumentos is None else list(argumentos)

    # Todo lo que sigue a `generar` es del generador (incluidas sus banderas y --help):
    # argparse no sabe repartir banderas desconocidas a un subcomando, asi que se corta aca.
    pasadas: list[str] = []
    for pasante in (
        "generar", "indicadores", "rasgos", "demo-cadena-viva", "usuarios", "cargar", "e2e",
    ):
        if pasante not in argumentos:
            continue
        corte = argumentos.index(pasante) + 1
        argumentos, pasadas = argumentos[:corte], argumentos[corte:]
        break

    args = analizador.parse_args(argumentos)
    args.argumentos = pasadas

    # Valores por omision para las tareas que no declaran estas banderas.
    for bandera, valor in (
        ("con_app", False),
        ("borrar_datos", False),
        ("tipo", "todas"),
        ("sin_mongo", False),
        ("verboso", False),
        ("si", False),
        ("front", False),
    ):
        if not hasattr(args, bandera):
            setattr(args, bandera, valor)

    cargar_entorno()
    try:
        args.funcion(args)
    except Fallo as fallo:
        print(f"\nERROR: {fallo}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
