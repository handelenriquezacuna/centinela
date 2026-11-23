"""H-09 · Latencia de deteccion: el calculo de percentiles y como D1 la mide.

Sin base de datos. La medicion contra Mongo de verdad (la hora sale del evento del
change stream) esta en `pruebas/integracion/prueba_latencia_mongo.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_supervisor
from app.deteccion.d1 import NOMBRE_D1, d1, latencia_ms
from app.main import crear_aplicacion
from app.nucleo.config import AjustesDeteccion
from app.repos.deteccion import EventoTransaccion
from app.supervision import Supervisor
from app.supervision.latencia import (
    CAPACIDAD_POR_OMISION,
    ResumenLatencia,
    VentanaLatencia,
    percentil,
    resumir,
)
from pruebas.dobles.datos_demo import TRANSACCIONES
from pruebas.dobles.repo_deteccion_falso import RepoDeteccionFalso, token
from pruebas.sesiones import encabezados_de

AJUSTES = AjustesDeteccion(
    espera_cursor_ms=10,
    reintentos=1,
    espera_inicial_segundos=0.001,
    espera_maxima_segundos=0.004,
)
INSERTADA = datetime(2026, 9, 25, 4, 47, 5, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Percentiles
# ---------------------------------------------------------------------------


def prueba_percentil_por_rango_mas_cercano() -> None:
    cien = [float(i) for i in range(1, 101)]
    assert percentil(cien, 50) == 50.0
    assert percentil(cien, 95) == 95.0
    assert percentil(cien, 100) == 100.0
    # Diez mediciones: ceil(0,5 * 10) = 5 y ceil(0,95 * 10) = 10.
    diez = [10.0 * i for i in range(1, 11)]
    assert percentil(diez, 50) == 50.0
    assert percentil(diez, 95) == 100.0


def prueba_con_una_sola_medicion_todo_es_esa_medicion() -> None:
    assert resumir([42.0]) == ResumenLatencia(n=1, p50=42.0, p95=42.0, max=42.0)


def prueba_el_resumen_no_depende_del_orden_de_llegada() -> None:
    desordenadas = [90.0, 10.0, 50.0, 30.0, 70.0]
    resumen = resumir(desordenadas)
    assert resumen == ResumenLatencia(n=5, p50=50.0, p95=90.0, max=90.0)
    assert desordenadas == [90.0, 10.0, 50.0, 30.0, 70.0], "no ordena la lista del llamador"


def prueba_un_valor_atipico_sube_p95_y_max_pero_no_p50() -> None:
    resumen = resumir([10.0] * 99 + [5000.0])
    assert resumen.p50 == 10.0
    assert resumen.p95 == 10.0
    assert resumen.max == 5000.0
    assert resumir([10.0] * 90 + [5000.0] * 10).p95 == 5000.0


def prueba_sin_mediciones_no_inventa_numeros() -> None:
    assert resumir([]) == ResumenLatencia(n=0, p50=None, p95=None, max=None)
    with pytest.raises(ValueError):
        percentil([], 50)


@pytest.mark.parametrize("porcentaje", [0, -5, 100.5])
def prueba_un_porcentaje_fuera_de_rango_se_rechaza(porcentaje: float) -> None:
    with pytest.raises(ValueError):
        percentil([1.0], porcentaje)


def prueba_la_ventana_conserva_solo_las_ultimas() -> None:
    ventana = VentanaLatencia(capacidad=3)
    for valor in (1000.0, 1.0, 2.0, 3.0):
        ventana.registrar(valor)
    assert len(ventana) == 3
    assert ventana.resumen() == ResumenLatencia(n=3, p50=2.0, p95=3.0, max=3.0)
    assert VentanaLatencia()._mediciones.maxlen == CAPACIDAD_POR_OMISION == 500


def prueba_un_desfase_negativo_de_relojes_se_registra_como_cero() -> None:
    ventana = VentanaLatencia()
    ventana.registrar(-12.0)
    assert ventana.resumen().max == 0.0


def prueba_latencia_ms_es_escritura_menos_insercion() -> None:
    assert latencia_ms(INSERTADA, INSERTADA + timedelta(milliseconds=1250)) == 1250.0
    assert latencia_ms(None, INSERTADA) is None


# ---------------------------------------------------------------------------
# D1 la mide
# ---------------------------------------------------------------------------


def _evento(numero: int, transaccion_id: str, *, insertada: datetime | None) -> EventoTransaccion:
    transaccion = next(t for t in TRANSACCIONES if t.transaccion_id == transaccion_id)
    return EventoTransaccion(
        token=token(numero), documento=transaccion.a_documento(), insertada_en=insertada
    )


async def _correr(repo: RepoDeteccionFalso, escrita_en: datetime) -> Supervisor:
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(repo, supervisor, ajustes=AJUSTES, reloj=lambda: escrita_en))
    try:
        await asyncio.wait_for(repo.agotado.wait(), timeout=5)
        return supervisor
    finally:
        await supervisor.detener()


@pytest.mark.asyncio
async def prueba_d1_mide_de_la_insercion_a_la_alerta_escrita() -> None:
    # TXN-001 genera alerta; TXN-002 no. Solo la primera tiene una escritura que medir.
    repo = RepoDeteccionFalso(
        [
            _evento(1, "TXN-001", insertada=INSERTADA),
            _evento(2, "TXN-002", insertada=INSERTADA),
        ]
    )
    supervisor = await _correr(repo, INSERTADA + timedelta(milliseconds=180))

    latencia = supervisor.informe().tarea(NOMBRE_D1).latencia
    assert latencia == ResumenLatencia(n=1, p50=180.0, p95=180.0, max=180.0)


@pytest.mark.asyncio
async def prueba_un_reproceso_no_se_mide() -> None:
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001", insertada=INSERTADA)], alertas_existentes=["TXN-001"]
    )
    supervisor = await _correr(repo, INSERTADA + timedelta(seconds=9))

    assert supervisor.informe().tarea(NOMBRE_D1).latencia.n == 0


@pytest.mark.asyncio
async def prueba_sin_hora_de_insercion_no_se_inventa_la_medicion() -> None:
    repo = RepoDeteccionFalso([_evento(1, "TXN-001", insertada=None)])
    supervisor = await _correr(repo, INSERTADA)

    assert supervisor.informe().tarea(NOMBRE_D1).latencia.n == 0


@pytest.mark.asyncio
async def prueba_d1_publica_latencia_desde_el_arranque_con_n_cero() -> None:
    repo = RepoDeteccionFalso([])
    supervisor = await _correr(repo, INSERTADA)

    assert supervisor.informe().tarea(NOMBRE_D1).latencia == ResumenLatencia(
        n=0, p50=None, p95=None, max=None
    )


# ---------------------------------------------------------------------------
# /estado la publica
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def cliente_con_d1() -> AsyncIterator[AsyncClient]:
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, asyncio.Event().wait())
    supervisor.lanzar("otra_tarea", asyncio.Event().wait())
    supervisor.latir(NOMBRE_D1)
    supervisor.latir("otra_tarea")
    supervisor.habilitar_latencia(NOMBRE_D1)
    for milisegundos in (100.0, 200.0, 300.0, 400.0, 2000.0):
        supervisor.registrar_latencia(NOMBRE_D1, milisegundos)

    aplicacion: FastAPI = crear_aplicacion()
    aplicacion.dependency_overrides[dep_supervisor] = lambda: supervisor
    try:
        async with AsyncClient(
            headers=encabezados_de(),
            transport=ASGITransport(app=aplicacion),
            base_url="http://pruebas",
        ) as cliente:
            yield cliente
    finally:
        await supervisor.detener()


@pytest.mark.asyncio
async def prueba_estado_publica_latencia_ms_solo_en_d1(cliente_con_d1: AsyncClient) -> None:
    cuerpo = (await cliente_con_d1.get("/estado")).json()
    tareas = {tarea["nombre"]: tarea for tarea in cuerpo["tareas"]}

    assert tareas[NOMBRE_D1]["latencia_ms"] == {"n": 5, "p50": 300.0, "p95": 2000.0, "max": 2000.0}
    assert tareas["otra_tarea"]["latencia_ms"] is None


def prueba_ritmo_y_estimado_del_rezago() -> None:
    """D-52: el estimado sale del ritmo medido, no de la fecha de negocio."""
    from datetime import UTC, datetime

    from app.supervision.rezago import ResumenRezago, ritmo

    assert ritmo(100, 10.0, 500, 14.0) == 100.0
    assert ritmo(100, 10.0, 100, 10.0) is None  # sin intervalo
    ahora = datetime(2026, 11, 23, tzinfo=UTC)
    assert ResumenRezago(600, None, ahora, 100.0).segundos_para_ponerse_al_dia == 6.0
    assert ResumenRezago(600, None, ahora, 0.0).segundos_para_ponerse_al_dia is None
    assert ResumenRezago(0, None, ahora, None).segundos_para_ponerse_al_dia == 0.0
