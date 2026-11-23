"""T-57 · Carga JSONL contra MongoDB de verdad: sin duplicar y con el validador activo.

Base propia, `antifraude_pruebas_carga` (ninguna prueba lee `antifraude`, T-34), con el
validador `$jsonSchema` real de `transacciones`: si la carga escribiera un documento que
no cumple el contrato, la base lo rechazaria y la prueba lo vera.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.carga.lotes import cargar
from app.repos.carga_transacciones import RepoCargaTransacciones
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from pruebas.conftest import BASE_SEMILLA, saltar_o_fallar

pytestmark = pytest.mark.mongo

BASE_CARGA = "antifraude_pruebas_carga"


@pytest_asyncio.fixture
async def base_carga(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    validadores = await obtener_validadores(cliente[BASE_SEMILLA])
    if "transacciones" not in validadores:
        saltar_o_fallar(f"{BASE_SEMILLA}.transacciones no tiene validador")
    await cliente.drop_database(BASE_CARGA)
    base = cliente[BASE_CARGA]
    await recrear_con_validador(base, "transacciones", validadores["transacciones"])
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_CARGA)


def _linea(numero: int, **cambios: Any) -> str:
    base: dict[str, Any] = {
        "_id": f"TXN-{numero:06d}",
        "cuenta_origen": "CTA-001",
        "cuenta_destino": "6033-9001",
        "monto_crc": 150_000,
        "fecha": "2026-11-23T14:05:00-06:00",
    }
    base.update(cambios)
    return json.dumps(base)


@pytest.mark.asyncio
async def prueba_carga_en_lotes_y_guarda_dinero_entero_y_fechas_utc(base_carga: Any) -> None:
    resumen = await cargar(
        RepoCargaTransacciones(base_carga), [_linea(n) for n in range(1, 2502)], tamano_lote=1000
    )

    assert (resumen.cargadas, resumen.repetidas, resumen.rechazadas) == (2501, 0, [])
    assert await base_carga["transacciones"].count_documents({}) == 2501
    guardada = await base_carga["transacciones"].find_one({"_id": "TXN-000001"})
    assert guardada["monto_crc"] == 150_000
    assert guardada["fecha"].utcoffset().total_seconds() == 0
    assert guardada["fecha"].hour == 20
    # El validador exige `long` para el dinero: Int64 en la base, aunque Python lea int.
    tipo = await base_carga["transacciones"].find_one(
        {"_id": "TXN-000001", "monto_crc": {"$type": "long"}}
    )
    assert tipo is not None


@pytest.mark.asyncio
async def prueba_volver_a_correr_el_archivo_no_duplica(base_carga: Any) -> None:
    repo = RepoCargaTransacciones(base_carga)
    lineas = [_linea(n) for n in range(1, 1501)]

    primera = await cargar(repo, lineas)
    segunda = await cargar(repo, lineas)

    assert (primera.cargadas, primera.repetidas) == (1500, 0)
    assert (segunda.cargadas, segunda.repetidas) == (0, 1500)
    assert segunda.rechazadas == []
    assert await base_carga["transacciones"].count_documents({}) == 1500


@pytest.mark.asyncio
async def prueba_un_archivo_a_medias_se_completa_al_reintentar(base_carga: Any) -> None:
    repo = RepoCargaTransacciones(base_carga)
    await cargar(repo, [_linea(n) for n in range(1, 701)])  # lo que alcanzo a cargar

    completo = await cargar(repo, [_linea(n) for n in range(1, 1501)])

    assert (completo.cargadas, completo.repetidas) == (800, 700)
    assert await base_carga["transacciones"].count_documents({}) == 1500


@pytest.mark.asyncio
async def prueba_una_repetida_dentro_del_mismo_archivo_cuenta_como_repetida(
    base_carga: Any,
) -> None:
    resumen = await cargar(RepoCargaTransacciones(base_carga), [_linea(1), _linea(2), _linea(1)])

    assert (resumen.cargadas, resumen.repetidas) == (2, 1)


@pytest.mark.asyncio
async def prueba_lo_que_la_base_rechaza_se_reporta_con_su_linea(base_carga: Any) -> None:
    """Pasa el modelo (monto 0 es un entero valido) pero no el validador (minimo 1)."""
    lineas = [_linea(1), _linea(2, monto_crc=0), _linea(3)]

    resumen = await cargar(RepoCargaTransacciones(base_carga), lineas)

    assert (resumen.cargadas, resumen.repetidas) == (2, 0)
    assert [rechazada.numero for rechazada in resumen.rechazadas] == [2]
    assert "la base la rechazo" in resumen.rechazadas[0].motivo
    assert await base_carga["transacciones"].count_documents({}) == 2
