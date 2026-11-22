"""H-23 · La migracion 002 (transacciones.sinpe_origen) contra MongoDB de verdad.

Corre el script tal cual (`mongosh --file`) sobre una base desechable con los
validadores reales de `transacciones` y `cuentas`, copiados de `antifraude`. Sostiene:

- las transacciones viejas (sin `sinpe_origen`) quedan con el SINPE de su cuenta de
  origen, y el validador real acepta la escritura del `$merge`;
- una transaccion cuya cuenta de origen no existe no se inventa: queda sin el campo y
  se lista;
- una segunda corrida no cambia nada y lo dice.

Necesita `mongosh` en el PATH (es como corre `python tareas.py migrar`).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from app.nucleo.config import Ajustes
from app.repos.carga_demo import a_entero_64
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from pruebas.conftest import saltar_o_fallar
from pruebas.dobles.datos_demo import DATOS_DEMO

pytestmark = pytest.mark.mongo

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "migraciones" / "002-transacciones-sinpe-origen.js"
BASE_DE_TRABAJO = "antifraude"
BASE_MIGRACION = "antifraude_pruebas_migracion_002"


@pytest_asyncio.fixture
async def base_migracion(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    validadores = await obtener_validadores(cliente[BASE_DE_TRABAJO])
    for nombre in ("transacciones", "cuentas"):
        if nombre not in validadores:
            saltar_o_fallar(f"sin validador de {nombre} en antifraude; correr 01-colecciones.js")
    if "sinpe_origen" not in str(validadores["transacciones"]):
        saltar_o_fallar(
            "el validador de transacciones en antifraude no conoce sinpe_origen; "
            "correr python tareas.py validadores"
        )

    await cliente.drop_database(BASE_MIGRACION)
    base = cliente[BASE_MIGRACION]
    for nombre in ("transacciones", "cuentas"):
        await recrear_con_validador(base, nombre, validadores[nombre])
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_MIGRACION)


async def _migrar(ajustes: Ajustes) -> str:
    mongosh = shutil.which("mongosh")
    if mongosh is None:
        saltar_o_fallar("mongosh no esta en el PATH; la migracion se corre con mongosh")
    resultado = await asyncio.to_thread(
        subprocess.run,
        [
            mongosh,
            ajustes.mongo.uri,
            "--quiet",
            "--eval",
            f'var BASE_MIGRACION="{BASE_MIGRACION}"',
            "--file",
            str(SCRIPT),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr + resultado.stdout
    return resultado.stdout


def _viejas() -> list[dict[str, Any]]:
    """Las transacciones del demo como estaban antes de H-23: sin `sinpe_origen`."""
    documentos = []
    for transaccion in DATOS_DEMO["transacciones"]:
        documento = a_entero_64(transaccion.a_documento())
        documento.pop("sinpe_origen", None)
        documentos.append(documento)
    return documentos


@pytest.mark.asyncio
async def prueba_rellena_sinpe_origen_y_la_segunda_corrida_no_cambia_nada(
    base_migracion: Any, ajustes: Ajustes
) -> None:
    await base_migracion["cuentas"].insert_many(
        [cuenta.a_documento() for cuenta in DATOS_DEMO["cuentas"]]
    )
    viejas = _viejas()
    huerfana = {
        **viejas[0],
        "_id": "TXN-900",
        "cuenta_origen": "CTA-999",
        "fecha": datetime(2026, 9, 25, tzinfo=UTC),
    }
    await base_migracion["transacciones"].insert_many([*viejas, huerfana])
    sinpe = {cuenta.cuenta_id: cuenta.cuenta_sinpe for cuenta in DATOS_DEMO["cuentas"]}

    primera = await _migrar(ajustes)
    print("\n" + primera)
    despues_de_la_primera = {
        documento["_id"]: documento
        async for documento in base_migracion["transacciones"].find({})
    }

    assert f"rellenadas: {len(viejas)}" in primera
    assert "ya lo tenian: 0" in primera
    assert "sin cuenta de origen, siguen sin el campo: 1 (TXN-900)" in primera
    for vieja in viejas:
        migrada = despues_de_la_primera[vieja["_id"]]
        assert migrada["sinpe_origen"] == sinpe[vieja["cuenta_origen"]]
        assert {clave: valor for clave, valor in migrada.items() if clave != "sinpe_origen"} == (
            vieja
        ), "solo se agrega el campo; nada mas cambia"
    assert "sinpe_origen" not in despues_de_la_primera["TXN-900"]

    segunda = await _migrar(ajustes)
    print(segunda)

    assert "rellenadas: 0" in segunda
    assert f"ya lo tenian: {len(viejas)}" in segunda
    assert {
        documento["_id"]: documento
        async for documento in base_migracion["transacciones"].find({})
    } == despues_de_la_primera
