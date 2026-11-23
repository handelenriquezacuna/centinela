"""Fixtures compartidas.

Dos cosas importantes que estan resueltas aqui y no en cada prueba:

1. `httpx.AsyncClient` NO dispara el lifespan de la aplicacion, y el cliente de Mongo
   se crea justamente en el lifespan. Por eso el cliente HTTP se arma envuelto en
   `LifespanManager` de `asgi-lifespan`. Sin eso, `dep_cliente` no encuentra nada en
   `aplicacion.state` y toda prueba de API falla con un RuntimeError confuso.
2. Las pruebas marcadas `mongo` se saltan solas, con un mensaje que dice como
   levantar el clúster, cuando el replica set no responde. Asi la compuerta de
   pruebas sigue siendo util con Docker apagado.
3. Modo estricto: con `CENTINELA_EXIGIR_MONGO=1` en el entorno, esos saltos se
   vuelven fallas. Es lo que usa el CI: alli "Mongo no responde" o "la base no esta
   sembrada" es un error del workflow, no una razon para salir en verde. Sin la
   variable, en local, todo sigue igual.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_repo_alertas
from app.main import crear_aplicacion
from app.nucleo.config import Ajustes, obtener_ajustes
from pruebas.dobles.datos_demo import DATOS_DEMO, alertas_como_documentos
from pruebas.dobles.repos_falsos import RepoAlertasFalso
from pruebas.sesiones import CLAVE_DE_PRUEBAS, encabezados_de

RUTA_RAIZ = Path(__file__).resolve().parents[1]

# Base aparte para las pruebas: nunca se toca la base de trabajo del equipo.
BASE_DE_PRUEBAS = "antifraude_pruebas"

# Semilla de referencia: 01-colecciones.js y 03-catalogos.js cargados en una base propia
# de las pruebas, no en `antifraude`. Las pruebas que necesitan los validadores reales o
# el catalogo leen de aqui, de modo que su resultado no depende de lo que el usuario
# haya hecho en la base de trabajo (migrar, generar, editar reglas).
BASE_SEMILLA = "antifraude_pruebas_semilla"
_SEMILLA_LISTA: set[str] = set()

_JS_SEMILLA = """
const destino = db.getMongo().getDB("%(base)s");
destino.dropDatabase();
db.getSiblingDB = function () { return destino; };
// 01 reasigna `db` a `destino` y 02 vuelve a llamar getSiblingDB sobre el: tambien se
// redirige, o los indices irian a `antifraude`.
destino.getSiblingDB = function () { return destino; };
print = function () {};
load("%(semilla)s/01-colecciones.js");
load("%(semilla)s/02-indices.js");
var BASE_CENTINELA = "%(base)s";
load("%(semilla)s/03-catalogos.js");
"""


async def _asegurar_semilla(uri: str) -> None:
    """Carga la semilla real (01, 02 y 03) en `BASE_SEMILLA`, una vez por proceso y URI."""
    if uri in _SEMILLA_LISTA:
        return
    import asyncio
    import shutil
    import subprocess

    mongosh = shutil.which("mongosh")
    if mongosh is None:
        saltar_o_fallar("mongosh no esta en el PATH; hace falta para sembrar la base de pruebas")
    js = _JS_SEMILLA % {"base": BASE_SEMILLA, "semilla": str(RUTA_RAIZ / "scripts" / "semilla")}
    resultado = await asyncio.to_thread(
        subprocess.run,
        [mongosh, uri, "--quiet", "--eval", js],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if resultado.returncode != 0:
        saltar_o_fallar(f"no se pudo sembrar {BASE_SEMILLA}: {resultado.stdout}{resultado.stderr}")
    _SEMILLA_LISTA.add(uri)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Las pruebas `resiliencia` solo corren si se piden por nombre (`-m resiliencia`).

    Levantan contenedores de Docker y tardan minutos: no pueden colarse en `pytest`, ni en
    `-m mongo`, ni en `-m "not mongo"`. Se DESELECCIONAN y no se saltan: un salto cuenta
    como prueba omitida y la compuerta de integracion exige cero saltos.
    """
    expresion = config.getoption("-m") or ""
    if "resiliencia" in expresion and "not resiliencia" not in expresion:
        return
    elegidas = [item for item in items if "resiliencia" not in item.keywords]
    deseleccionadas = [item for item in items if "resiliencia" in item.keywords]
    if deseleccionadas:
        config.hook.pytest_deselected(items=deseleccionadas)
        items[:] = elegidas


@pytest.fixture(autouse=True)
def limpiar_cache_de_ajustes() -> Iterator[None]:
    """`obtener_ajustes` esta memorizado; entre pruebas hay que soltar la memoria.

    Es autouse porque olvidarlo produce el peor tipo de falla: una prueba que pasa
    sola y falla cuando corre despues de otra.
    """
    obtener_ajustes.cache_clear()
    yield
    obtener_ajustes.cache_clear()


@pytest.fixture(autouse=True)
def clave_de_sesion(monkeypatch: pytest.MonkeyPatch) -> None:
    """La clave con que `pruebas/sesiones.py` firma los tokens y la app los valida."""
    monkeypatch.setenv("CENTINELA_SESION__CLAVE", CLAVE_DE_PRUEBAS)


@pytest.fixture
def ajustes() -> Ajustes:
    """Ajustes reales, leidos de `config/centinela.yml`, con la base de pruebas."""
    return Ajustes(mongo={"uri": _uri_del_repo(), "base": BASE_DE_PRUEBAS})


def _uri_del_repo() -> str:
    """URI del YAML del repo, sin duplicarla en el codigo de prueba."""
    return Ajustes().mongo.uri


@pytest.fixture
def repo_alertas_falso() -> RepoAlertasFalso:
    return RepoAlertasFalso(alertas_como_documentos())


@pytest.fixture
def aplicacion(repo_alertas_falso: RepoAlertasFalso) -> FastAPI:
    """Aplicacion con el repositorio de alertas sustituido por el doble."""
    creada = crear_aplicacion()
    creada.dependency_overrides[dep_repo_alertas] = lambda: repo_alertas_falso
    return creada


@pytest_asyncio.fixture
async def cliente(aplicacion: FastAPI) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app en memoria, con el lifespan disparado."""
    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app),
        base_url="http://pruebas",
        headers=encabezados_de(),
    ) as cliente_http:
        yield cliente_http


# ---------------------------------------------------------------------------
# Lo que necesita Mongo
# ---------------------------------------------------------------------------

MENSAJE_SIN_MONGO = (
    "el replica set rsfraude no responde. Levantarlo con: python tareas.py arriba"
)

# Variable del modo estricto. Vive en pruebas/ y no en app/nucleo/config.py a
# proposito: no es configuracion de la aplicacion sino de como se corre el suite, y
# la regla "solo config.py lee el entorno" es sobre app/.
VARIABLE_EXIGIR_MONGO = "CENTINELA_EXIGIR_MONGO"


def exigir_mongo() -> bool:
    """True si el entorno pide que la falta de Mongo sea una falla y no un salto."""
    return os.environ.get(VARIABLE_EXIGIR_MONGO, "") == "1"


def saltar_o_fallar(motivo: str) -> None:
    """Salta la prueba en local; la hace fallar en modo estricto.

    Un salto silencioso en CI es la peor salida posible: el job queda verde sin haber
    probado nada. Por eso, con la variable activa, el mismo motivo se reporta como
    falla y dice como se apaga el modo estricto.
    """
    if exigir_mongo():
        pytest.fail(
            f"{motivo}\n(modo estricto: {VARIABLE_EXIGIR_MONGO}=1 convierte este salto "
            "en falla; sin la variable la prueba se salta)",
            pytrace=False,
        )
    pytest.skip(motivo)


# Memoria del sondeo, por URI. Sin esto, con Mongo apagado cada prueba marcada
# `mongo` paga el tiempo de espera de seleccion de servidor completo antes de saltarse,
# y la compuerta de pruebas pasa de segundos a minutos.
_SONDEO: dict[str, bool] = {}


async def _mongo_responde(ajustes_mongo: Ajustes) -> bool:
    if ajustes_mongo.mongo.uri in _SONDEO:
        return _SONDEO[ajustes_mongo.mongo.uri]

    from app.nucleo.db import ciclo_cliente

    try:
        async with ciclo_cliente(ajustes_mongo) as cliente_mongo:
            await cliente_mongo.admin.command("ping")
        responde = True
    except Exception:  # noqa: BLE001 - sondea si Mongo responde, sin tumbar la prueba
        responde = False

    _SONDEO[ajustes_mongo.mongo.uri] = responde
    return responde


@pytest_asyncio.fixture
async def base_mongo(ajustes: Ajustes) -> AsyncIterator[Any]:
    """Base de datos de pruebas, ya conectada. Salta la prueba si Mongo no esta
    (o falla, en modo estricto)."""
    from app.nucleo.db import ciclo_cliente

    if not await _mongo_responde(ajustes):
        saltar_o_fallar(MENSAJE_SIN_MONGO)

    await _asegurar_semilla(ajustes.mongo.uri)

    async with ciclo_cliente(ajustes) as cliente_mongo:
        yield cliente_mongo[ajustes.mongo.base]


@pytest_asyncio.fixture
async def base_con_demo(base_mongo: Any) -> Any:
    """Base de pruebas con el conjunto de demo cargado."""
    from app.repos.carga_demo import cargar_demo

    await cargar_demo(base_mongo, DATOS_DEMO)
    return base_mongo


@pytest_asyncio.fixture
async def cliente_con_mongo(
    ajustes: Ajustes, base_con_demo: Any
) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra Mongo de verdad, con el demo cargado.

    No sustituye el repositorio: aca se ejercita el camino completo
    ruta -> dependencia -> repositorio -> MongoDB.
    """
    os.environ["CENTINELA_MONGO__BASE"] = ajustes.mongo.base
    obtener_ajustes.cache_clear()
    try:
        creada = crear_aplicacion()
        async with LifespanManager(creada) as gestionada, AsyncClient(
            transport=ASGITransport(app=gestionada.app),
            base_url="http://pruebas",
            headers=encabezados_de(),
        ) as cliente_http:
            yield cliente_http
    finally:
        os.environ.pop("CENTINELA_MONGO__BASE", None)
        obtener_ajustes.cache_clear()
