"""Fixtures compartidas.

Dos cosas importantes que estan resueltas aqui y no en cada prueba:

1. `httpx.AsyncClient` NO dispara el lifespan de la aplicacion, y el cliente de Mongo
   se crea justamente en el lifespan. Por eso el cliente HTTP se arma envuelto en
   `LifespanManager` de `asgi-lifespan`. Sin eso, `dep_cliente` no encuentra nada en
   `aplicacion.state` y toda prueba de API falla con un RuntimeError confuso.
2. Las pruebas marcadas `mongo` se saltan solas, con un mensaje que dice como
   levantar el clúster, cuando el replica set no responde. Asi la compuerta de
   pruebas sigue siendo util con Docker apagado.
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

RUTA_RAIZ = Path(__file__).resolve().parents[1]

# Base aparte para las pruebas: nunca se toca la base de trabajo del equipo.
BASE_DE_PRUEBAS = "antifraude_pruebas"


@pytest.fixture(autouse=True)
def limpiar_cache_de_ajustes() -> Iterator[None]:
    """`obtener_ajustes` esta memorizado; entre pruebas hay que soltar la memoria.

    Es autouse porque olvidarlo produce el peor tipo de falla: una prueba que pasa
    sola y falla cuando corre despues de otra.
    """
    obtener_ajustes.cache_clear()
    yield
    obtener_ajustes.cache_clear()


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
    ) as cliente_http:
        yield cliente_http


# ---------------------------------------------------------------------------
# Lo que necesita Mongo
# ---------------------------------------------------------------------------

MENSAJE_SIN_MONGO = (
    "el replica set rsfraude no responde. Levantarlo con: python tareas.py arriba"
)


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
    """Base de datos de pruebas, ya conectada. Salta la prueba si Mongo no esta."""
    from app.nucleo.db import ciclo_cliente

    if not await _mongo_responde(ajustes):
        pytest.skip(MENSAJE_SIN_MONGO)

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
        ) as cliente_http:
            yield cliente_http
    finally:
        os.environ.pop("CENTINELA_MONGO__BASE", None)
        obtener_ajustes.cache_clear()
