"""H-22 contra MongoDB de verdad: `$merge` idempotente, validador real, D3.

Base propia, `antifraude_pruebas_indicadores`, que se tira al terminar. Los validadores
de `alertas` e `indicadores_diarios` se copian de la base sembrada de pruebas
(`BASE_SEMILLA`, que `base_mongo` carga con el `scripts/semilla/01-colecciones.js` de la
rama; T-34: ninguna prueba lee `antifraude`): si el pipeline escribiera algo que el contrato
rechaza, `$merge` falla y la prueba con el.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from bson import Int64

from app.deteccion.d3 import NOMBRE_D3, d3
from app.nucleo.config import AjustesIndicadores
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.indicadores import RepoIndicadores, hoy_costa_rica
from app.supervision import Supervisor
from pruebas.conftest import BASE_SEMILLA, saltar_o_fallar

pytestmark = pytest.mark.mongo

BASE_INDICADORES = "antifraude_pruebas_indicadores"
CON_VALIDADOR = ("alertas", "indicadores_diarios")


def _alerta(
    n: int, severidad: str, estado: str, fecha: datetime, agente: str | None = None
) -> dict[str, Any]:
    doc = {
        "_id": f"ALR-9{n:03d}",
        "transaccion_id": f"TXN-9{n:03d}",
        "cuenta_origen": "8712-4455",
        "cuenta_destino": "6033-9001",
        "monto_crc": Int64(100000),
        "moneda": "CRC",
        "cliente_nombre": "Cliente de prueba",
        "reglas_disparadas": ["REG-001"],
        "puntaje": 80,
        "severidad": severidad,
        "estado": estado,
        "fecha_creacion": fecha,
        "historial": [{"accion": "nueva", "agente_id": None, "fecha": fecha}],
        "esquema_version": 1,
    }
    if agente:
        doc["agente_id"] = agente
    return doc


def _utc(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 11, dia, hora, minuto, tzinfo=UTC)


# 21-nov en Costa Rica va de 06:00 UTC del 21 a 06:00 UTC del 22.
ALERTAS = [
    _alerta(1, "CRITICA", "nueva", _utc(21, 5, 59)),  # 20-nov 23:59 en CR
    _alerta(2, "ALTA", "confirmada", _utc(21, 6, 0), "AGT-001"),  # 21-nov 00:00 en CR
    _alerta(3, "CRITICA", "confirmada", _utc(21, 20), "AGT-001"),
    _alerta(4, "MEDIA", "descartada", _utc(22, 5, 59)),  # 21-nov 23:59 en CR
    _alerta(5, "ALTA", "escalada", _utc(22, 6, 0)),  # 22-nov 00:00 en CR
]


@pytest_asyncio.fixture
async def base_indicadores(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    validadores = await obtener_validadores(cliente[BASE_SEMILLA])
    faltan = [nombre for nombre in CON_VALIDADOR if nombre not in validadores]
    if faltan:
        saltar_o_fallar(
            f"sin validador en {BASE_SEMILLA} para {faltan}; correr "
            "scripts/semilla/01-colecciones.js"
        )
    await cliente.drop_database(BASE_INDICADORES)
    base = cliente[BASE_INDICADORES]
    for nombre in CON_VALIDADOR:
        await recrear_con_validador(base, nombre, validadores[nombre])
    await base["alertas"].insert_many(ALERTAS)
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_INDICADORES)


async def _sin_marca(base: Any) -> list[dict[str, Any]]:
    docs = await base["indicadores_diarios"].find({}).sort("_id", 1).to_list(length=None)
    for doc in docs:
        doc.pop("calculado_en")
    return docs


@pytest.mark.asyncio
async def prueba_el_corte_del_dia_y_los_conteos(base_indicadores: Any) -> None:
    repo = RepoIndicadores(base_indicadores)

    await repo.recalcular(date(2026, 11, 20), date(2026, 11, 23))

    docs = {d["_id"]: d for d in await _sin_marca(base_indicadores)}
    assert list(docs) == ["2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"]
    assert docs["2026-11-20"]["total_alertas"] == 1
    assert docs["2026-11-20"]["por_severidad"] == {"MEDIA": 0, "ALTA": 0, "CRITICA": 1}
    assert docs["2026-11-21"]["total_alertas"] == 3
    assert docs["2026-11-21"]["por_severidad"] == {"MEDIA": 1, "ALTA": 1, "CRITICA": 1}
    assert docs["2026-11-21"]["por_estado"] == {
        "nueva": 0,
        "en_revision": 0,
        "confirmada": 2,
        "descartada": 1,
        "escalada": 0,
    }
    assert docs["2026-11-22"]["total_alertas"] == 1
    assert docs["2026-11-23"]["total_alertas"] == 0, "el dia sin alertas queda en ceros"


@pytest.mark.asyncio
async def prueba_dos_corridas_dan_los_mismos_documentos(base_indicadores: Any) -> None:
    repo = RepoIndicadores(base_indicadores)

    await repo.recalcular(date(2026, 11, 20), date(2026, 11, 23))
    primera = await _sin_marca(base_indicadores)
    marca_1 = (await base_indicadores["indicadores_diarios"].find_one({"_id": "2026-11-21"}))[
        "calculado_en"
    ]
    await asyncio.sleep(0.01)
    await repo.recalcular(date(2026, 11, 20), date(2026, 11, 23))
    segunda = await _sin_marca(base_indicadores)
    marca_2 = (await base_indicadores["indicadores_diarios"].find_one({"_id": "2026-11-21"}))[
        "calculado_en"
    ]

    assert primera == segunda
    assert await base_indicadores["indicadores_diarios"].count_documents({}) == 4
    assert marca_2 >= marca_1


@pytest.mark.asyncio
async def prueba_una_alerta_nueva_actualiza_el_dia_sin_duplicar(base_indicadores: Any) -> None:
    repo = RepoIndicadores(base_indicadores)
    await repo.recalcular(date(2026, 11, 21), date(2026, 11, 21))

    await base_indicadores["alertas"].insert_one(_alerta(6, "MEDIA", "nueva", _utc(21, 12)))
    await repo.recalcular(date(2026, 11, 21), date(2026, 11, 21))

    docs = await base_indicadores["indicadores_diarios"].find({}).to_list(length=None)
    assert len(docs) == 1
    assert docs[0]["total_alertas"] == 4
    assert docs[0]["por_severidad"]["MEDIA"] == 2


@pytest.mark.asyncio
async def prueba_d3_arranca_y_escribe_el_dia_de_hoy(base_indicadores: Any) -> None:
    ahora = datetime.now(UTC)
    await base_indicadores["alertas"].insert_one(_alerta(7, "ALTA", "nueva", ahora))
    hoy = hoy_costa_rica(ahora).isoformat()

    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(
        NOMBRE_D3,
        d3(
            RepoIndicadores(base_indicadores),
            supervisor,
            ajustes=AjustesIndicadores(intervalo_segundos=0.1),
        ),
    )
    try:
        for _ in range(50):
            if supervisor.informe().tarea(NOMBRE_D3).eventos_procesados >= 1:
                break
            await asyncio.sleep(0.1)
        doc = await base_indicadores["indicadores_diarios"].find_one({"_id": hoy})
        assert doc is not None
        assert doc["total_alertas"] >= 1
        assert supervisor.informe().tarea(NOMBRE_D3).estado.value == "corriendo"
    finally:
        await supervisor.detener()


@pytest.mark.asyncio
async def prueba_por_hora_y_por_agente_en_hora_de_costa_rica(base_indicadores: Any) -> None:
    repo = RepoIndicadores(base_indicadores)
    await repo.recalcular(date(2026, 11, 20), date(2026, 11, 23))

    docs = {d["_id"]: d for d in await _sin_marca(base_indicadores)}
    # Alerta a las 23:59 de CR del 20-nov: cuenta en la hora "23" de ese dia.
    assert docs["2026-11-20"]["por_hora"]["23"] == 1
    assert docs["2026-11-20"]["por_agente"] == {"sin_asignar": 1}
    dia = docs["2026-11-21"]
    assert list(dia["por_hora"]) == [f"{h:02d}" for h in range(24)]
    assert dia["por_hora"]["00"] == 1  # 06:00 UTC
    assert dia["por_hora"]["14"] == 1  # 20:00 UTC
    assert dia["por_hora"]["23"] == 1  # 05:59 UTC del 22
    assert sum(dia["por_hora"].values()) == dia["total_alertas"] == 3
    assert dia["por_agente"] == {"AGT-001": 2, "sin_asignar": 1}
    assert docs["2026-11-23"]["por_agente"] == {}
    assert set(docs["2026-11-23"]["por_hora"].values()) == {0}


@pytest.mark.asyncio
async def prueba_el_validador_real_acepta_y_rechaza_los_campos_nuevos(
    base_indicadores: Any,
) -> None:
    from pymongo.errors import WriteError

    repo = RepoIndicadores(base_indicadores)
    await repo.recalcular(date(2026, 11, 21), date(2026, 11, 21))
    doc = await base_indicadores["indicadores_diarios"].find_one({"_id": "2026-11-21"})
    assert doc is not None  # el $merge ya paso por el validador

    # Claves que no son agente ni hora siguen prohibidas.
    for campo, valor in (("por_agente", {"pepe": 1}), ("por_hora", {"25x": 1})):
        malo = {**doc, "_id": "2026-12-01", "fecha": "2026-12-01", campo: valor}
        with pytest.raises(WriteError):
            await base_indicadores["indicadores_diarios"].insert_one(malo)
    # Sin los opcionales (documentos viejos, como la semilla) sigue valido.
    viejo = {k: v for k, v in doc.items() if k not in ("por_agente", "por_hora")}
    viejo.update(_id="2026-12-02", fecha="2026-12-02")
    await base_indicadores["indicadores_diarios"].insert_one(viejo)


def _con_historial(
    n: int, estado: str, creada: datetime, monto: int, pasos: list[tuple[str, int]]
) -> dict[str, Any]:
    """Alerta con el historial dado: `pasos` = (accion, segundos despues de la creacion)."""
    alerta = _alerta(n, "ALTA", estado, creada)
    alerta["monto_crc"] = Int64(monto)
    alerta["historial"] = [
        {"accion": "nueva", "agente_id": None, "fecha": creada},
        *[
            {"accion": accion, "agente_id": "AGT-001", "fecha": creada + timedelta(seconds=seg)}
            for accion, seg in pasos
        ],
    ]
    return alerta


KPI_ALERTAS = [
    # 25-nov en CR. Decision a los 600 s (la primera terminal; la siguiente no cuenta).
    _con_historial(
        101, "confirmada", _utc(25, 10), 100_000,
        [("en_revision", 60), ("confirmada", 600), ("escalada", 900)],
    ),
    _con_historial(102, "descartada", _utc(25, 11), 40_000, [("en_revision", 30), ("descartada", 200)]),
    _con_historial(103, "escalada", _utc(25, 12), 250_000, [("en_revision", 30), ("escalada", 1000)]),
    _con_historial(104, "nueva", _utc(25, 14), 70_000, []),
    _con_historial(105, "en_revision", _utc(25, 8), 90_000, [("en_revision", 120)]),
]


@pytest.mark.asyncio
async def prueba_los_kpi_del_supervisor_salen_del_historial_y_son_idempotentes(
    base_indicadores: Any,
) -> None:
    await base_indicadores["alertas"].insert_many(KPI_ALERTAS)
    repo = RepoIndicadores(base_indicadores)

    await repo.recalcular(date(2026, 11, 20), date(2026, 11, 26))
    primera = await _sin_marca(base_indicadores)
    await repo.recalcular(date(2026, 11, 20), date(2026, 11, 26))
    segunda = await _sin_marca(base_indicadores)

    assert primera == segunda  # dos corridas, los mismos documentos
    dias = {d["_id"]: d for d in primera}
    dia = dias["2026-11-25"]
    assert dia["total_alertas"] == 5
    # Tres con transicion terminal: 600 + 200 + 1000 segundos.
    assert (dia["decision_cantidad"], dia["decision_segundos_total"]) == (3, 1800)
    # Confirmada + escalada; la descartada y las abiertas no protegen nada.
    assert dia["monto_protegido_crc"] == 100_000 + 250_000
    assert dia["pendientes"] == 2
    assert dia["pendiente_mas_antigua"].replace(tzinfo=UTC) == _utc(25, 8)
    # Un dia sin alertas no inventa nada: ceros y sin "mas antigua".
    vacio = dias["2026-11-26"]
    assert (vacio["decision_cantidad"], vacio["monto_protegido_crc"], vacio["pendientes"]) == (0, 0, 0)
    assert "pendiente_mas_antigua" not in vacio


@pytest.mark.asyncio
async def prueba_un_dia_sin_alertas_resueltas_no_tiene_segundos_ni_division(
    base_indicadores: Any,
) -> None:
    repo = RepoIndicadores(base_indicadores)
    await repo.recalcular(date(2026, 11, 20), date(2026, 11, 20))
    dia = await base_indicadores["indicadores_diarios"].find_one({"_id": "2026-11-20"})

    # La alerta 1 del dia sigue `nueva`: pendiente, sin decision.
    assert (dia["decision_cantidad"], dia["decision_segundos_total"]) == (0, 0)
    assert dia["pendientes"] == 1
    assert dia["pendiente_mas_antigua"].replace(tzinfo=UTC) == _utc(21, 5, 59)


@pytest.mark.asyncio
async def prueba_el_validador_real_acepta_los_kpi_y_rechaza_valores_invalidos(
    base_indicadores: Any,
) -> None:
    from pymongo.errors import WriteError

    await RepoIndicadores(base_indicadores).recalcular(date(2026, 11, 21), date(2026, 11, 21))
    doc = await base_indicadores["indicadores_diarios"].find_one({"_id": "2026-11-21"})
    assert {"decision_cantidad", "monto_protegido_crc", "pendientes"} <= set(doc)

    for campo, valor in (
        ("monto_protegido_crc", -1),
        ("pendientes", "dos"),
        ("pendiente_mas_antigua", "ayer"),
    ):
        malo = {**doc, "_id": "2026-12-03", "fecha": "2026-12-03", campo: valor}
        with pytest.raises(WriteError):
            await base_indicadores["indicadores_diarios"].insert_one(malo)
