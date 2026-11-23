"""T-52 · Failover: D1 sobrevive a la caida del primario sin duplicar ni perder alertas.

Levanta su PROPIO replica set de tres nodos en Docker (puertos 27272 a 27274; no toca
el cluster del compose ni la base de trabajo), con D1 procesando, y mata al primario
mientras entran transacciones. Despues comprueba:

1. que otro nodo fue elegido primario;
2. que cada transaccion con riesgo, insertada antes, durante o despues de la caida,
   tiene exactamente una alerta, y que ninguna cayo al descarte;
3. que D1 reanuda desde `deteccion_estado`: se detiene, entran transacciones con D1
   abajo, vuelve y las evalua; y con el token atrasado (como si hubiera muerto entre la
   alerta y el token) el reproceso no reescribe ninguna alerta.

Como se corre (un comando, y solo a pedido; `pytest` a secas la deja afuera):

    python tareas.py resiliencia          o          pytest -m resiliencia -s

Necesita Docker y la imagen mongo:8. Tarda entre uno y dos minutos y no entra al CI: ver
docs/06-plataforma.md, "Failover de D1 y compuerta manual".

Topologia: los tres mongod comparten la red de un contenedor "ancla" que solo duerme y
publica los puertos. Es el mismo truco del compose del repo (todos ven localhost:2727x),
pero con el ancla aparte: si el nodo que se mata fuera el dueño de la red, caerian los
tres. Maximo cuatro contenedores propios a la vez (ancla + 3 nodos).
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from app.deteccion.d1 import NOMBRE_D1, d1
from app.nucleo.config import Ajustes
from app.repos.carga_demo import a_entero_64
from app.repos.deteccion import RepoDeteccion
from app.supervision import Supervisor
from pruebas.integracion.prueba_d1_mongo import (  # noqa: F401 - base_d1 es un fixture
    BASE_D1,
    _alertas_por_transaccion,
    _insertar,
    _noche,
    _oficina,
    base_d1,
)

pytestmark = pytest.mark.resiliencia

IMAGEN = "mongo:8"
ANCLA = "t52f-red"
PUERTOS = (27272, 27273, 27274)
URI = "mongodb://localhost:27272,localhost:27273,localhost:27274/?replicaSet=rsfraude"
# Eleccion mas rapida que el valor por omision (10 s) para que la prueba no dure minutos.
# Es una configuracion de la prueba; el cluster real usa el valor por omision del servidor.
ELECCION_MS = 2000
LIMITE_SEGUNDOS = 120


def nodo(puerto: int) -> str:
    return f"t52f-n{puerto}"


def _docker(*argumentos: str, tolerar: bool = False) -> str:
    resultado = subprocess.run(
        ["docker", *argumentos], capture_output=True, text=True, timeout=120, check=False
    )
    if resultado.returncode != 0 and not tolerar:
        raise RuntimeError(f"docker {' '.join(argumentos)}: {resultado.stderr.strip()}")
    return resultado.stdout.strip()


def _derribar() -> None:
    for nombre in [nodo(p) for p in PUERTOS] + [ANCLA]:
        _docker("rm", "-f", nombre, tolerar=True)


def _hello(puerto: int) -> dict[str, Any] | None:
    """`hello` directo a un nodo, o None si no contesta."""
    cliente: MongoClient = MongoClient(
        "localhost", puerto, directConnection=True, serverSelectionTimeoutMS=1500
    )
    try:
        return cliente.admin.command("hello")
    except Exception:  # noqa: BLE001 - un nodo caido es un dato, no un error
        return None
    finally:
        cliente.close()


def _primario(excluir: int | None = None) -> int | None:
    for puerto in PUERTOS:
        if puerto == excluir:
            continue
        hello = _hello(puerto)
        if hello is not None and hello.get("isWritablePrimary"):
            return puerto
    return None


def _todos_sanos() -> bool:
    estados = [_hello(puerto) for puerto in PUERTOS]
    return all(
        hello is not None and (hello.get("isWritablePrimary") or hello.get("secondary"))
        for hello in estados
    )


def _esperar_primario(excluir: int | None = None, segundos: int = 60) -> tuple[int, float]:
    inicio = time.monotonic()
    while time.monotonic() - inicio < segundos:
        puerto = _primario(excluir)
        if puerto is not None:
            return puerto, time.monotonic() - inicio
        time.sleep(0.25)
    raise AssertionError(f"ningun primario nuevo en {segundos} s (excluyendo {excluir})")


@pytest.fixture(scope="module")
def cluster_de_tres() -> Iterator[None]:
    """Ancla + tres mongod con replica set rsfraude, sin autenticacion. Se baja al final."""
    try:
        _docker("version", "--format", "{{.Server.Version}}")
    except Exception as fallo:  # noqa: BLE001
        pytest.fail(f"la prueba de failover necesita Docker: {fallo}", pytrace=False)
    _derribar()
    try:
        _docker(
            "run", "-d", "--rm", "--name", ANCLA,
            "-p", "127.0.0.1:27272-27274:27272-27274",
            "--entrypoint", "sleep", IMAGEN, "infinity",
        )  # fmt: skip
        for puerto in PUERTOS:
            _docker(
                "run", "-d", "--rm", "--name", nodo(puerto), "--network", f"container:{ANCLA}",
                IMAGEN, "mongod", "--replSet", "rsfraude", "--port", str(puerto),
                "--bind_ip_all", "--wiredTigerCacheSizeGB", "0.25",
            )  # fmt: skip
        miembros = ",".join(f'{{_id:{i},host:"localhost:{p}"}}' for i, p in enumerate(PUERTOS))
        inicio = (
            f'rs.initiate({{_id:"rsfraude",members:[{miembros}],'
            f"settings:{{electionTimeoutMillis:{ELECCION_MS}}}}})"
        )
        limite = time.monotonic() + 60
        while True:  # mongod tarda unos segundos en aceptar conexiones
            try:
                _docker("exec", nodo(PUERTOS[0]), "mongosh", "--port", str(PUERTOS[0]),
                        "--quiet", "--eval", inicio)  # fmt: skip
                break
            except RuntimeError:
                if time.monotonic() > limite:
                    raise
                time.sleep(1)
        _esperar_primario()
        # Los tres nodos tienen que estar sanos (primario o secundario) antes de matar a uno.
        limite = time.monotonic() + 60
        while not _todos_sanos():
            if time.monotonic() > limite:
                raise AssertionError("los tres nodos no llegaron a PRIMARY/SECONDARY")
            time.sleep(0.5)
        yield
    finally:
        _derribar()


@pytest.fixture
def ajustes(cluster_de_tres: None) -> Ajustes:
    """Pisa la fixture global: toda la infraestructura de pruebas apunta al cluster de tres."""
    return Ajustes(mongo={"uri": URI, "base": "antifraude_pruebas"})


@asynccontextmanager
async def _d1_real(base: Any) -> AsyncIterator[Supervisor]:
    """D1 como en el lifespan y con los reintentos reales del YAML (no los de prueba)."""
    ajustes_d1 = Ajustes().deteccion
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(RepoDeteccion(base), supervisor, ajustes=ajustes_d1))

    async def abierto() -> bool:
        return supervisor.informe().tarea(NOMBRE_D1).eventos_procesados >= 1

    try:
        await _hasta(abierto, "el primer latido de D1")
        yield supervisor
    finally:
        await supervisor.detener()


async def _hasta(condicion: Any, que: str, segundos: int = LIMITE_SEGUNDOS) -> None:
    limite = asyncio.get_running_loop().time() + segundos
    while not await condicion():
        if asyncio.get_running_loop().time() > limite:
            raise AssertionError(f"pasaron {segundos} s esperando {que}")
        await asyncio.sleep(0.1)


async def _procesada(base: Any, transaccion_id: str) -> None:
    async def llego() -> bool:
        estado = await base["deteccion_estado"].find_one({"_id": NOMBRE_D1})
        return estado is not None and estado.get("ultima_transaccion_id") == transaccion_id

    await _hasta(llego, f"que D1 guarde el token de {transaccion_id}")


def _transaccion(numero: int) -> Any:
    """Impares con riesgo (R-01 + R-03 + R-05), pares de oficina sin alerta."""
    transaccion_id = f"TXN-{numero:03d}"
    if numero % 2:
        return _noche(1 + numero % 27, transaccion_id, f"7200-{numero:04d}")
    return _oficina(1 + numero % 27, transaccion_id)


def _con_riesgo(numeros: range) -> set[str]:
    return {f"TXN-{n:03d}" for n in numeros if n % 2}


async def _insertar_una_a_una(base: Any, numeros: range, *, pausa: float = 0.0) -> None:
    """Inserta de a una y reintenta si no hay primario: asi las inserciones quedan
    repartidas antes, durante y despues de la eleccion. Un duplicado es exito (una
    escritura ambigua que si se aplico)."""
    for numero in numeros:
        documento = a_entero_64(_transaccion(numero).a_documento())
        limite = asyncio.get_running_loop().time() + LIMITE_SEGUNDOS
        while True:
            try:
                await base["transacciones"].insert_one(documento)
                break
            except DuplicateKeyError:
                break
            except Exception:
                if asyncio.get_running_loop().time() > limite:
                    raise
                await asyncio.sleep(0.25)
        if pausa:
            await asyncio.sleep(pausa)


@pytest.mark.asyncio
async def prueba_d1_sobrevive_a_la_caida_del_primario(base_d1: Any, capsys: Any) -> None:  # noqa: F811
    log: list[str] = []

    def anotar(texto: str) -> None:
        log.append(texto)
        with capsys.disabled():
            print(f"[failover] {texto}", flush=True)

    primario_inicial = _primario()
    assert primario_inicial is not None
    anotar(f"primario inicial: localhost:{primario_inicial}")

    async with _d1_real(base_d1) as supervisor:
        # Fase 1: antes de la caida.
        await _insertar_una_a_una(base_d1, range(1, 21))
        await _procesada(base_d1, "TXN-020")
        estado_tras_a = await base_d1["deteccion_estado"].find_one({"_id": NOMBRE_D1})
        anotar(f"fase A: 20 transacciones procesadas, {len(_con_riesgo(range(1, 21)))} con alerta")

        # Fase 2: la caida, con transacciones entrando todo el tiempo.
        entrada = asyncio.create_task(_insertar_una_a_una(base_d1, range(21, 51), pausa=0.2))
        await asyncio.sleep(1.5)
        antes_del_kill = await base_d1["transacciones"].count_documents({})
        momento = time.monotonic()
        _docker("kill", nodo(primario_inicial))
        anotar(
            f"docker kill {nodo(primario_inicial)} (primario); "
            f"{antes_del_kill} de 50 transacciones ya insertadas, el resto entra despues"
        )
        primario_nuevo, _ = await asyncio.to_thread(
            _esperar_primario, primario_inicial
        )
        anotar(
            f"nuevo primario: localhost:{primario_nuevo} "
            f"({time.monotonic() - momento:.1f} s despues del kill)"
        )
        assert primario_nuevo != primario_inicial, "no se eligio otro primario"
        await entrada
        await _procesada(base_d1, "TXN-050")
        anotar("fase B: 30 transacciones insertadas antes, durante y despues; D1 al dia")
        error = supervisor.informe().tarea(NOMBRE_D1).ultimo_error
        anotar(f"ultimo error de D1 durante el failover: {error.mensaje if error else 'ninguno'}")

    # Fase 3: reanudacion desde deteccion_estado, ya con el nuevo primario.
    await _insertar_una_a_una(base_d1, range(51, 61))  # D1 abajo
    esperadas_antes = _con_riesgo(range(1, 51))
    assert set(await _alertas_por_transaccion(base_d1)) == esperadas_antes, (
        "con D1 abajo no nace ninguna alerta"
    )
    async with _d1_real(base_d1):
        await _procesada(base_d1, "TXN-060")
    todas = _con_riesgo(range(1, 61))
    assert await _alertas_por_transaccion(base_d1) == {t: 1 for t in todas}
    anotar("fase C: D1 reanudo desde el token y evaluo las 10 que entraron con D1 abajo")

    # Fase 4: el token queda atras (murio entre la alerta y el token): se reprocesa todo
    # desde la fase A y el indice unico rechaza cada duplicado.
    antes = {
        d["_id"]: d["fecha_creacion"] async for d in base_d1["alertas"].find({})
    }
    await base_d1["deteccion_estado"].replace_one({"_id": NOMBRE_D1}, estado_tras_a)
    async with _d1_real(base_d1) as supervisor:
        await _procesada(base_d1, "TXN-060")
        error_final = supervisor.informe().tarea(NOMBRE_D1).ultimo_error
    despues = {
        d["_id"]: d["fecha_creacion"] async for d in base_d1["alertas"].find({})
    }
    anotar(f"fase D: token atrasado a TXN-020, reproceso de 40 eventos; alertas={len(despues)}")

    assert await _alertas_por_transaccion(base_d1) == {t: 1 for t in todas}
    assert despues == antes, "el reproceso no reescribe alertas existentes"
    assert error_final is None, f"un reproceso es normal, no un error: {error_final}"
    assert await base_d1["deteccion_estado"].count_documents({"clase": "descarte"}) == 0
    anotar(
        f"RESULTADO: {len(todas)} transacciones con riesgo, {len(todas)} alertas, 0 duplicadas, "
        f"0 perdidas, 0 en descarte; BASE={BASE_D1} hora={datetime.now(UTC):%H:%M:%S}Z"
    )
