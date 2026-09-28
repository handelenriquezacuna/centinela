"""`GET /estado`: el contrato de la ruta de estado funcional.

Lo que estas pruebas sostienen es la diferencia entre las dos rutas de operacion:
`/salud` dice "el proceso vive y la base contesta" y `/estado` dice "el sistema esta
haciendo su trabajo". Un solo endpoint que mezclara las dos responderia 200 mientras el
detector lleva dos horas sin procesar una transaccion.

El supervisor se sustituye por dependencia, con tareas de verdad pero triviales: asi la
prueba controla el escenario (una tarea viva, una tarea muerta, ninguna tarea) sin
depender de Mongo ni de esperas.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_supervisor
from app.main import crear_aplicacion
from app.supervision import DISPARADORES_PREVISTOS, NOMBRE_LATIDO, Supervisor

RUTA = "/estado"


async def _nunca_termina() -> None:
    await asyncio.sleep(3600)


@pytest_asyncio.fixture
async def supervisor() -> AsyncIterator[Supervisor]:
    """Supervisor con una tarea viva, la misma que monta el lifespan de verdad."""
    creado = Supervisor(tolerancia_segundos=30)
    creado.lanzar(NOMBRE_LATIDO, _nunca_termina())
    creado.latir(NOMBRE_LATIDO)
    yield creado
    await creado.detener()


@pytest_asyncio.fixture
async def cliente_estado(supervisor: Supervisor) -> AsyncIterator[AsyncClient]:
    """Cliente contra la aplicacion con ese supervisor inyectado.

    No hace falta el lifespan: `/estado` no consulta la base, lee el informe. Es
    justamente lo que la ruta promete, asi que la prueba puede correrlo sin Mongo.
    """
    aplicacion: FastAPI = crear_aplicacion()
    aplicacion.dependency_overrides[dep_supervisor] = lambda: supervisor
    async with AsyncClient(
        transport=ASGITransport(app=aplicacion), base_url="http://pruebas"
    ) as cliente:
        yield cliente


@pytest.mark.asyncio
async def prueba_responde_la_forma_completa(cliente_estado: AsyncClient) -> None:
    respuesta = await cliente_estado.get(RUTA)

    assert respuesta.status_code == status.HTTP_200_OK
    cuerpo = respuesta.json()

    assert set(cuerpo) == {
        "estado",
        "version",
        "entorno",
        "tolerancia_retraso_segundos",
        "tareas",
    }
    assert cuerpo["estado"] == "ok"
    assert cuerpo["tolerancia_retraso_segundos"] == 30


@pytest.mark.asyncio
async def prueba_cada_tarea_publica_evento_retraso_y_error(
    cliente_estado: AsyncClient,
) -> None:
    """Los cuatro datos que pide el criterio, por tarea y con un solo formato."""
    cuerpo = (await cliente_estado.get(RUTA)).json()
    fila = next(t for t in cuerpo["tareas"] if t["nombre"] == NOMBRE_LATIDO)

    assert set(fila) == {
        "nombre",
        "estado",
        "eventos_procesados",
        "ultimo_evento",
        "retraso_segundos",
        "ultimo_error",
        "historia",
    }
    assert fila["estado"] == "corriendo"
    assert fila["eventos_procesados"] == 1
    assert fila["ultimo_evento"] is not None
    assert fila["retraso_segundos"] is not None
    assert fila["ultimo_error"] is None


@pytest.mark.asyncio
async def prueba_reporta_los_tres_disparadores(cliente_estado: AsyncClient) -> None:
    """D1, D2 y D3 se reportan, y se reportan como lo que son: sin construir.

    Es el criterio "reporta D1/D2/D3" cumplido sin inventar disparadores: cada fila
    dice `no_construida` y trae la historia que la va a traer.
    """
    cuerpo = (await cliente_estado.get(RUTA)).json()
    filas = {t["nombre"]: t for t in cuerpo["tareas"]}

    assert set(DISPARADORES_PREVISTOS) <= set(filas)
    for nombre, historia in DISPARADORES_PREVISTOS.items():
        assert filas[nombre]["estado"] == "no_construida"
        assert filas[nombre]["historia"] == historia

    assert cuerpo["estado"] == "ok", "lo que falta por construir no degrada el sistema"


@pytest.mark.asyncio
async def prueba_matar_la_tarea_deja_el_estado_degradado(
    cliente_estado: AsyncClient, supervisor: Supervisor
) -> None:
    """El criterio de la historia, visto por HTTP."""
    assert (await cliente_estado.get(RUTA)).json()["estado"] == "ok"

    await supervisor.detener_tarea(NOMBRE_LATIDO)

    cuerpo = (await cliente_estado.get(RUTA)).json()
    assert cuerpo["estado"] == "degradado"
    fila = next(t for t in cuerpo["tareas"] if t["nombre"] == NOMBRE_LATIDO)
    assert fila["estado"] == "detenida"


@pytest.mark.asyncio
async def prueba_sin_tareas_el_estado_no_dice_ok() -> None:
    """Una aplicacion sin lifespan no tiene tareas, y no puede decir que todo va bien."""
    aplicacion = crear_aplicacion()
    aplicacion.dependency_overrides[dep_supervisor] = lambda: Supervisor(
        tolerancia_segundos=30
    )

    async with AsyncClient(
        transport=ASGITransport(app=aplicacion), base_url="http://pruebas"
    ) as cliente:
        cuerpo = (await cliente.get(RUTA)).json()

    assert cuerpo["estado"] == "sin_supervision"


@pytest.mark.asyncio
async def prueba_estado_no_es_salud(cliente: AsyncClient) -> None:
    """Dos rutas distintas con dos respuestas distintas, no un alias de la otra.

    Va contra el cliente con lifespan de `conftest.py`, no con el supervisor
    sustituido, por dos razones: `/salud` necesita el cliente de Mongo que crea el
    lifespan, y asi la prueba ademas comprueba que las dos rutas conviven en la
    aplicacion de verdad.
    """
    estado = (await cliente.get(RUTA)).json()
    salud = (await cliente.get("/salud")).json()

    assert "tareas" in estado and "tareas" not in salud
    assert "mongo_conectado" in salud and "mongo_conectado" not in estado
    assert estado["version"] == salud["version"]
