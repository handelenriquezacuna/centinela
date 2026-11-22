"""H-03, H-04 y H-05 contra los validadores REALES de la base.

Se copian los validadores de `antifraude` (los que siembra `scripts/semilla/01-colecciones.js`)
a la base de pruebas y ahi se escribe lo que genera el CLI. Si el generador escribiera un
documento que el contrato rechaza, `insert_many` lanzaria y la prueba fallaria.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest
from bson import Int64
from pymongo.errors import BulkWriteError, WriteError

from app.generador.ejecucion import ejecutar
from app.nucleo.config import AjustesGenerador
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.generador import RepoGenerador
from pruebas.conftest import saltar_o_fallar

pytestmark = pytest.mark.mongo

BASE_DE_TRABAJO = "antifraude"
COLECCIONES = (
    "clientes",
    "cuentas",
    "perfiles_comportamiento",
    "transacciones",
    "listas_riesgo",
)


async def _preparar(base: Any) -> None:
    validadores = await obtener_validadores(base.client[BASE_DE_TRABAJO])
    for coleccion in COLECCIONES:
        if coleccion not in validadores:
            saltar_o_fallar(f"{BASE_DE_TRABAJO}.{coleccion} no tiene validador: sembrar 01-colecciones.js")
        await recrear_con_validador(base, coleccion, validadores[coleccion])


@pytest.mark.asyncio
async def prueba_200_clientes_y_5000_transacciones_pasan_el_validador_real(
    base_mongo: Any,
) -> None:
    await _preparar(base_mongo)

    resumen = await ejecutar(RepoGenerador(base_mongo), AjustesGenerador(clientes=200, transacciones=5000))

    assert await base_mongo.clientes.count_documents({}) == 200
    assert await base_mongo.cuentas.count_documents({}) == 200
    assert await base_mongo.perfiles_comportamiento.count_documents({}) == 200
    assert await base_mongo.transacciones.count_documents({}) == 5000
    assert resumen.transacciones == 5000
    # Las mulas reportadas entraron por el validador real de listas_riesgo.
    assert resumen.listas_riesgo >= 1
    assert await base_mongo.listas_riesgo.count_documents({}) == resumen.listas_riesgo
    assert 0 < resumen.total_fraude < 100  # menos del 2 % de 5.000
    assert set(resumen.fraude_por_patron) == {
        "monto_atipico_destino_nuevo",
        "rafaga_fragmentada",
        "cadena_mulas",
    }
    # El dinero llego como long de 64 bits, no como int32 ni double.
    assert await base_mongo.transacciones.count_documents(
        {"monto_crc": {"$not": {"$type": "long"}}}
    ) == 0
    assert await base_mongo.perfiles_comportamiento.count_documents(
        {"promedio_crc": {"$not": {"$type": "long"}}}
    ) == 0


@pytest.mark.asyncio
async def prueba_correr_dos_veces_deja_lo_mismo_y_no_toca_el_demo(base_mongo: Any) -> None:
    await _preparar(base_mongo)
    await base_mongo.clientes.insert_one(
        {
            "_id": "CLI-001",
            "nombre": "Cliente de demo",
            "cedula": "1-1111-1111",
            "fecha_registro": datetime(2025, 1, 15, tzinfo=UTC),
            "esquema_version": 1,
        }
    )
    ajustes = AjustesGenerador(clientes=20, transacciones=500)

    await ejecutar(RepoGenerador(base_mongo), ajustes)
    primera = await base_mongo.transacciones.find_one({"_id": "TXN-000001"})
    await ejecutar(RepoGenerador(base_mongo), ajustes)
    segunda = await base_mongo.transacciones.find_one({"_id": "TXN-000001"})

    assert primera == segunda
    assert await base_mongo.listas_riesgo.count_documents({"_id": {"$regex": "^RSK-[0-9]{4}"}}) == (
        await base_mongo.listas_riesgo.count_documents({})
    )
    assert await base_mongo.transacciones.count_documents({}) == 500
    assert await base_mongo.clientes.count_documents({}) == 21
    assert await base_mongo.clientes.count_documents({"_id": "CLI-001"}) == 1


@pytest.mark.asyncio
async def prueba_el_ritmo_configurado_reparte_la_insercion_en_el_tiempo(base_mongo: Any) -> None:
    await _preparar(base_mongo)
    ajustes = AjustesGenerador(clientes=10, transacciones=100, ritmo_por_segundo=200)

    inicio = time.monotonic()
    await ejecutar(RepoGenerador(base_mongo), ajustes)

    # 100 documentos a 200 por segundo: no menos de medio segundo.
    assert time.monotonic() - inicio >= 0.45


@pytest.mark.asyncio
async def prueba_el_validador_acepta_id_largo_y_etiqueta_y_rechaza_lo_malformado(
    base_mongo: Any,
) -> None:
    await _preparar(base_mongo)
    base = {
        "_id": "TXN-123456",
        "cuenta_origen": "CTA-0001",
        "cuenta_destino": "2233-4455",
        "monto_crc": Int64(10_000),
        "moneda": "CRC",
        "canal": "sinpe_movil",
        "fecha": datetime(2026, 11, 1, 15, 0, tzinfo=UTC),
        "esquema_version": 1,
    }
    etiqueta = {"fraude": True, "patron": "rafaga_fragmentada", "episodio": "EPI-0001"}

    await base_mongo.transacciones.insert_one({**base, "simulacion": etiqueta})

    malos = [
        {**base, "_id": "TXN-123457", "simulacion": {**etiqueta, "patron": "inventado"}},
        {**base, "_id": "TXN-123458", "simulacion": {"patron": "cadena_mulas"}},
        {**base, "_id": "TXN-123459", "simulacion": {**etiqueta, "extra": 1}},
        {**base, "_id": "TXN-123460", "monto_crc": 10_000.5},
        {**base, "_id": "TXN-12"},
    ]
    for malo in malos:
        with pytest.raises((WriteError, BulkWriteError)):
            await base_mongo.transacciones.insert_one(malo)

    # La cuenta con entidad pasa; una entidad vacia, no.
    cuenta = {"_id": "CTA-12345", "cuenta_sinpe": "7777-8888", "cliente_id": "CLI-12345", "esquema_version": 1}
    await base_mongo.cuentas.insert_one({**cuenta, "entidad": "Entidad D"})
    with pytest.raises(WriteError):
        await base_mongo.cuentas.insert_one({**cuenta, "_id": "CTA-12346", "entidad": ""})
