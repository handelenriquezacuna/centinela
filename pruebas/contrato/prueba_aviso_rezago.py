"""T-57: el rezago de D1 en `/estado` y el aviso del panel, sin Mongo.

El supervisor se inyecta con una tarea D1 trivial y una medicion cargada a mano: lo que
se prueba es el contrato (campo de `/estado`, umbral configurable, texto accesible del
aviso), no la consulta, que prueba `pruebas/integracion/prueba_rezago_mongo.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_supervisor
from app.api.rutas.paginas import antiguedad_legible
from app.deteccion.d1 import NOMBRE_D1
from app.main import crear_aplicacion
from app.nucleo.config import obtener_ajustes
from app.supervision import Supervisor
from pruebas.sesiones import encabezados_de

AHORA = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


async def _nunca_termina() -> None:
    await asyncio.sleep(3600)


@pytest_asyncio.fixture
async def supervisor() -> AsyncIterator[Supervisor]:
    creado = Supervisor(tolerancia_segundos=30)
    creado.lanzar(NOMBRE_D1, _nunca_termina())
    creado.latir(NOMBRE_D1)
    yield creado
    await creado.detener()


@pytest_asyncio.fixture
async def cliente(supervisor: Supervisor) -> AsyncIterator[AsyncClient]:
    aplicacion: FastAPI = crear_aplicacion()
    aplicacion.dependency_overrides[dep_supervisor] = lambda: supervisor
    async with AsyncClient(
        headers=encabezados_de(),
        transport=ASGITransport(app=aplicacion),
        base_url="http://pruebas",
    ) as http:
        yield http


def _umbral() -> int:
    return obtener_ajustes().deteccion.rezago_aviso_desde


@pytest.mark.asyncio
async def prueba_estado_publica_el_rezago_de_d1(
    cliente: AsyncClient, supervisor: Supervisor
) -> None:
    supervisor.registrar_rezago(NOMBRE_D1, 1234, 95.0, AHORA)

    cuerpo = (await cliente.get("/estado")).json()

    fila = next(t for t in cuerpo["tareas"] if t["nombre"] == NOMBRE_D1)
    assert fila["rezago"] == {
        "pendientes": 1234,
        "antiguedad_segundos": 95.0,
        "ritmo_por_segundo": None,
        "segundos_para_ponerse_al_dia": None,
        "atraso_segundos": None,
        "medido_en": "2026-11-23T12:00:00Z",
    }


@pytest.mark.asyncio
async def prueba_estado_publica_el_atraso_en_segundos(
    cliente: AsyncClient, supervisor: Supervisor
) -> None:
    supervisor.registrar_rezago(NOMBRE_D1, 0, None, AHORA, atraso_segundos=42.5)

    fila = next(
        t for t in (await cliente.get("/estado")).json()["tareas"] if t["nombre"] == NOMBRE_D1
    )

    assert fila["rezago"]["atraso_segundos"] == 42.5


@pytest.mark.asyncio
async def prueba_solo_con_atraso_el_aviso_lo_dice_sin_inventar_un_conteo(
    cliente: AsyncClient, supervisor: Supervisor
) -> None:
    """La red de seguridad: el conteo dice 0 (ids fuera de orden) pero D1 va atrasada."""
    supervisor.registrar_rezago(NOMBRE_D1, 0, None, AHORA, atraso_segundos=125.0)

    texto = (await cliente.get("/panel/rezago")).text

    assert "Detección atrasada" in texto
    assert "va unos 2 min detrás de la ingesta" in texto
    assert "transacciones por evaluar" not in texto


@pytest.mark.asyncio
async def prueba_con_conteo_y_atraso_gana_el_mensaje_del_conteo(
    cliente: AsyncClient, supervisor: Supervisor
) -> None:
    supervisor.registrar_rezago(NOMBRE_D1, _umbral() + 1, None, AHORA, atraso_segundos=125.0)

    texto = (await cliente.get("/panel/rezago")).text

    assert "transacciones por evaluar" in texto
    assert "detrás de la ingesta" not in texto


@pytest.mark.asyncio
async def prueba_en_el_umbral_de_atraso_no_hay_aviso(
    cliente: AsyncClient, supervisor: Supervisor
) -> None:
    desde = obtener_ajustes().deteccion.rezago_aviso_atraso_segundos
    supervisor.registrar_rezago(NOMBRE_D1, 0, None, AHORA, atraso_segundos=desde)

    assert "Detección atrasada" not in (await cliente.get("/panel/rezago")).text


@pytest.mark.asyncio
async def prueba_sin_medicion_el_rezago_es_nulo(cliente: AsyncClient) -> None:
    cuerpo = (await cliente.get("/estado")).json()

    fila = next(t for t in cuerpo["tareas"] if t["nombre"] == NOMBRE_D1)
    assert fila["rezago"] is None


@pytest.mark.asyncio
async def prueba_el_aviso_aparece_sobre_el_umbral_con_cantidad_y_estimado(
    cliente: AsyncClient, supervisor: Supervisor
) -> None:
    """D-52: el analista ve cuanto falta, no la fecha de negocio de la mas vieja."""
    pendientes = _umbral() + 1
    supervisor.registrar_rezago(NOMBRE_D1, pendientes + 400, 9e6, AHORA, monotono=100.0)
    for _ in range(400):  # D1 evalua 400 en 4 s: 100 por segundo
        supervisor.contar_evaluada(NOMBRE_D1)
    supervisor.registrar_rezago(NOMBRE_D1, pendientes, 9e6, AHORA, monotono=104.0)

    respuesta = await cliente.get("/panel/rezago")

    assert respuesta.status_code == 200
    assert "Detección atrasada" in respuesta.text
    assert f"{pendientes} transacciones por evaluar" in respuesta.text
    assert "unos 5 s para ponerse al día" in respuesta.text
    assert "hace" not in respuesta.text  # nada de la fecha de negocio

    fila = next(
        t for t in (await cliente.get("/estado")).json()["tareas"] if t["nombre"] == NOMBRE_D1
    )
    assert fila["rezago"]["ritmo_por_segundo"] == 100.0


@pytest.mark.asyncio
async def prueba_sin_ritmo_medido_el_aviso_lo_dice(
    cliente: AsyncClient, supervisor: Supervisor
) -> None:
    supervisor.registrar_rezago(NOMBRE_D1, _umbral() + 1, 125.0, AHORA)

    texto = (await cliente.get("/panel/rezago")).text

    assert "todavía sin ritmo medido" in texto


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [0, -1])
async def prueba_en_el_umbral_o_debajo_no_hay_aviso(
    cliente: AsyncClient, supervisor: Supervisor, delta: int
) -> None:
    supervisor.registrar_rezago(NOMBRE_D1, _umbral() + delta, 10.0, AHORA)

    assert "Detección atrasada" not in (await cliente.get("/panel/rezago")).text


@pytest.mark.asyncio
async def prueba_sin_medicion_no_hay_aviso(cliente: AsyncClient) -> None:
    assert "Detección atrasada" not in (await cliente.get("/panel/rezago")).text


@pytest.mark.asyncio
async def prueba_el_panel_trae_la_region_accesible_con_polling_suave(
    cliente: AsyncClient,
) -> None:
    # El panel pide su cola a Mongo; aqui solo se mira el HTML de la region del aviso,
    # que no depende de ella. Se lee la plantilla renderizada por la pagina real.
    from pathlib import Path

    plantilla = (
        Path(__file__).resolve().parents[2] / "app" / "plantillas" / "paginas" / "panel.html"
    ).read_text(encoding="utf-8")
    assert 'id="aviso-rezago"' in plantilla
    assert 'role="status"' in plantilla and 'aria-live="polite"' in plantilla
    assert 'hx-get="/panel/rezago"' in plantilla and "every 10s" in plantilla


def prueba_la_antiguedad_se_lee_en_la_unidad_mayor() -> None:
    assert antiguedad_legible(None) == ""
    assert antiguedad_legible(7.9) == "7 s"
    assert antiguedad_legible(125) == "2 min"
    assert antiguedad_legible(7300) == "2 h"
    assert antiguedad_legible(3 * 86400 + 5) == "3 d"
