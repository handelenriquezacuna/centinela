"""H-22 sin base de datos: el armado del pipeline, el corte del dia y el bucle de D3."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest

from app.deteccion.d3 import d3
from app.deteccion.indicadores import leer_argumentos
from app.nucleo.config import AjustesIndicadores
from app.repos.indicadores import (
    ESTADOS,
    SEVERIDADES,
    construir_pipeline,
    dias_del_rango,
    documento_en_ceros,
    hoy_costa_rica,
    limites_utc,
)
from app.supervision import Supervisor


def prueba_el_pipeline_tiene_las_cinco_etapas_en_orden() -> None:
    pipeline = construir_pipeline(date(2026, 11, 20), date(2026, 11, 22))

    assert [next(iter(etapa)) for etapa in pipeline] == [
        "$match",
        "$group",
        "$sort",
        "$project",
        "$merge",
    ]


def prueba_el_merge_reemplaza_por_id_y_es_idempotente() -> None:
    merge = construir_pipeline(date(2026, 11, 22), date(2026, 11, 22))[-1]["$merge"]

    assert merge["into"] == "indicadores_diarios"
    assert merge["on"] == "_id"
    assert merge["whenMatched"] == "replace"
    assert merge["whenNotMatched"] == "insert"


def prueba_el_dia_se_corta_en_hora_de_costa_rica() -> None:
    inicio, fin = limites_utc(date(2026, 11, 21), date(2026, 11, 21))

    # Medianoche de CR (UTC-6) es 06:00 UTC.
    assert inicio == datetime(2026, 11, 21, 6, tzinfo=UTC)
    assert fin == datetime(2026, 11, 22, 6, tzinfo=UTC)

    etapas = construir_pipeline(date(2026, 11, 21), date(2026, 11, 21))
    assert etapas[0]["$match"]["fecha_creacion"] == {"$gte": inicio, "$lt": fin}
    clave = etapas[1]["$group"]["_id"]["$dateToString"]
    assert clave["timezone"] == "-06:00"
    assert clave["format"] == "%Y-%m-%d"


def prueba_el_proyecto_solo_emite_lo_que_acepta_el_validador() -> None:
    proyecto = construir_pipeline(date(2026, 11, 22), date(2026, 11, 22))[3]["$project"]

    assert set(proyecto) == {
        "_id",
        "fecha",
        "total_alertas",
        "por_severidad",
        "por_estado",
        "por_agente",
        "por_hora",
        "calculado_en",
        "esquema_version",
    }
    assert set(proyecto["por_severidad"]) == set(SEVERIDADES)
    assert set(proyecto["por_estado"]) == set(ESTADOS)


def prueba_el_rango_invertido_se_rechaza() -> None:
    with pytest.raises(ValueError):
        limites_utc(date(2026, 11, 22), date(2026, 11, 21))


def prueba_dias_del_rango_incluye_los_dos_extremos() -> None:
    assert dias_del_rango(date(2026, 11, 29), date(2026, 12, 2)) == [
        "2026-11-29",
        "2026-11-30",
        "2026-12-01",
        "2026-12-02",
    ]


def prueba_hoy_en_costa_rica_no_es_el_dia_utc_de_noche() -> None:
    # 03:00 UTC del 22 es las 21:00 del 21 en Costa Rica.
    assert hoy_costa_rica(datetime(2026, 11, 22, 3, tzinfo=UTC)) == date(2026, 11, 21)


def prueba_el_documento_en_ceros_tiene_la_forma_del_pipeline() -> None:
    ahora = datetime(2026, 11, 22, tzinfo=UTC)
    doc = documento_en_ceros("2026-11-22", ahora)

    assert doc["_id"] == doc["fecha"] == "2026-11-22"
    assert doc["total_alertas"] == 0
    assert set(doc["por_severidad"]) == set(SEVERIDADES)
    assert set(doc["por_estado"]) == set(ESTADOS)
    assert doc["por_agente"] == {}
    assert list(doc["por_hora"]) == [f"{h:02d}" for h in range(24)]
    assert set(doc["por_hora"].values()) == {0}
    assert doc["calculado_en"] == ahora


def prueba_los_argumentos_del_cli() -> None:
    assert leer_argumentos(["--desde", "2026-11-01", "--hasta", "2026-11-22"]) == (
        date(2026, 11, 1),
        date(2026, 11, 22),
    )
    desde, hasta = leer_argumentos([])
    assert desde == hasta
    with pytest.raises(SystemExit):
        leer_argumentos(["--desde", "2026-11-01"])
    with pytest.raises(SystemExit):
        leer_argumentos(["--desde", "2026-11-05", "--hasta", "2026-11-01"])


class _RepoFalso:
    def __init__(self, fallos: int = 0) -> None:
        self.llamadas: list[tuple[date, date]] = []
        self.fallos = fallos

    async def recalcular(self, desde: date, hasta: date) -> int:
        self.llamadas.append((desde, hasta))
        if len(self.llamadas) <= self.fallos:
            raise RuntimeError("la base no responde")
        return 1


@pytest.mark.asyncio
async def prueba_d3_recalcula_el_dia_actual_y_late() -> None:
    supervisor = Supervisor(tolerancia_segundos=10)
    repo = _RepoFalso()
    tarea = supervisor.lanzar(
        "d3_indicadores",
        d3(
            repo,
            supervisor,
            ajustes=AjustesIndicadores(intervalo_segundos=0.01),
            hoy=lambda: date(2026, 11, 22),
        ),
    )
    await asyncio.sleep(0.1)

    fila = supervisor.informe().tarea("d3_indicadores")
    assert fila.estado.value == "corriendo"
    assert fila.eventos_procesados >= 2
    assert set(repo.llamadas) == {(date(2026, 11, 22), date(2026, 11, 22))}
    assert not tarea.done()
    await supervisor.detener()


@pytest.mark.asyncio
async def prueba_d3_sobrevive_a_un_fallo_y_sigue() -> None:
    supervisor = Supervisor(tolerancia_segundos=10)
    repo = _RepoFalso(fallos=1)
    supervisor.lanzar(
        "d3_indicadores",
        d3(
            repo,
            supervisor,
            ajustes=AjustesIndicadores(intervalo_segundos=0.01),
            hoy=lambda: date(2026, 11, 22),
        ),
    )
    await asyncio.sleep(0.1)

    fila = supervisor.informe().tarea("d3_indicadores")
    assert fila.eventos_procesados >= 1, "despues del fallo vuelve a calcular"
    assert len(repo.llamadas) >= 2
    await supervisor.detener()
