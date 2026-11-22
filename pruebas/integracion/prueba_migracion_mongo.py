"""H-26 · La migracion 001 (alertas v1 -> v2) contra MongoDB de verdad.

Corre el script tal cual (`mongosh --file`), sobre una base desechable con el
validador real de `alertas` y el catalogo real de reglas y politica, copiados de
`antifraude`. Lo que sostiene:

- ALR-001 sembrada en v1 queda en v2 con las 3 reglas embebidas (version, peso y
  explicacion reconstruida desde la regla) y la politica, y pasa el validador;
- una segunda corrida no cambia nada (idempotente) y lo dice;
- una alerta v1 escrita por D1 toma la version de su nota; si esa version ya no
  existe, la explicacion es "no disponible: regla editada" y se cuenta;
- una regla editada despues de la alerta: se usa la version que regia entonces.

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

from app.modelos import Alerta
from app.nucleo.config import Ajustes
from app.repos.carga_demo import a_entero_64
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from pruebas.conftest import BASE_SEMILLA, saltar_o_fallar
from pruebas.dobles.datos_demo import DATOS_DEMO

pytestmark = pytest.mark.mongo

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "migraciones" / "001-alertas-v1-a-v2.js"
BASE_DE_TRABAJO = BASE_SEMILLA  # semilla propia de las pruebas, no `antifraude`
BASE_MIGRACION = "antifraude_pruebas_migracion"


@pytest_asyncio.fixture
async def base_migracion(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    trabajo = cliente[BASE_DE_TRABAJO]
    validadores = await obtener_validadores(trabajo)
    if "alertas" not in validadores:
        saltar_o_fallar("sin validador de alertas en antifraude; correr 01-colecciones.js")

    await cliente.drop_database(BASE_MIGRACION)
    base = cliente[BASE_MIGRACION]
    await recrear_con_validador(base, "alertas", validadores["alertas"])
    for nombre in ("reglas_deteccion", "politica_deteccion"):
        documentos = await trabajo[nombre].find({}).to_list(length=None)
        if not documentos:
            saltar_o_fallar(f"{BASE_DE_TRABAJO}.{nombre} vacia: la semilla de pruebas no cargo")
        await base[nombre].insert_many(documentos)
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


def _alr_001_v1() -> dict[str, Any]:
    documento = a_entero_64(DATOS_DEMO["alertas"][0].a_documento())
    assert documento["_id"] == "ALR-001" and documento["esquema_version"] == 1
    return documento


@pytest.mark.asyncio
async def prueba_convierte_alr_001_y_la_segunda_corrida_no_cambia_nada(
    base_migracion: Any, ajustes: Ajustes
) -> None:
    await base_migracion["alertas"].insert_one(_alr_001_v1())

    primera = await _migrar(ajustes)
    migrada = await base_migracion["alertas"].find_one({"_id": "ALR-001"})

    assert "migradas: 1" in primera
    assert "ya estaban en v2: 0" in primera
    assert migrada["esquema_version"] == 2
    assert [
        (r["regla_id"], r["codigo"], r["version"], r["peso"])
        for r in migrada["reglas_disparadas"]
    ] == [("REG-001", "R-01", 1, 35), ("REG-002", "R-03", 1, 25), ("REG-003", "R-05", 1, 15)]
    assert all(r["explicacion"].startswith(r["codigo"]) for r in migrada["reglas_disparadas"])
    assert migrada["politica"] == {"politica_id": "POL-001", "version": 1}
    # El historial y lo demas no se tocan; la lectura dual la lee como v2.
    assert migrada["historial"] == _alr_001_v1()["historial"]
    assert Alerta.model_validate(migrada).reglas_aplicadas[0].peso == 35

    segunda = await _migrar(ajustes)

    assert "migradas: 0" in segunda
    assert "ya estaban en v2: 1" in segunda
    assert await base_migracion["alertas"].find_one({"_id": "ALR-001"}) == migrada


@pytest.mark.asyncio
async def prueba_la_version_de_la_nota_manda_y_si_no_existe_se_dice(
    base_migracion: Any, ajustes: Ajustes
) -> None:
    """Una v1 de D1 dice con que version se evaluo. REG-001 v7 no existe: no se
    inventa la explicacion con la vigente."""
    documento = {
        **_alr_001_v1(),
        "_id": "ALR-002",
        "transaccion_id": "TXN-002",
        "historial": [
            {
                "accion": "nueva",
                "agente_id": None,
                "fecha": datetime(2026, 9, 25, 4, 47, tzinfo=UTC),
                "nota": (
                    "Creada por D1 con POL-001 v1; reglas: R-01 REG-001 v7 (+35), "
                    "R-03 REG-002 v1 (+25), R-05 REG-003 v1 (+15)"
                ),
            }
        ],
    }
    await base_migracion["alertas"].insert_one(documento)

    salida = await _migrar(ajustes)
    migrada = await base_migracion["alertas"].find_one({"_id": "ALR-002"})

    assert "reglas con explicacion no disponible (regla editada): 1" in salida
    primera = migrada["reglas_disparadas"][0]
    assert (primera["version"], primera["peso"]) == (7, 35)
    assert primera["explicacion"] == "no disponible: regla editada"
    assert migrada["reglas_disparadas"][1]["explicacion"].startswith("R-03 v1")


@pytest.mark.asyncio
async def prueba_usa_la_version_que_regia_cuando_nacio_la_alerta(
    base_migracion: Any, ajustes: Ajustes
) -> None:
    """D-14: REG-001 se edito despues de ALR-001 (peso 35 -> 40). La alerta se explica
    con la v1 guardada en `versiones_anteriores`, no con la vigente."""
    reglas = base_migracion["reglas_deteccion"]
    actual = await reglas.find_one({"_id": "REG-001"})
    anterior = {k: v for k, v in actual.items() if k not in {"_id", "versiones_anteriores"}}
    anterior["reemplazada_en"] = datetime(2026, 10, 1, tzinfo=UTC)
    await reglas.update_one(
        {"_id": "REG-001"},
        {"$set": {"version": 2, "peso": 40}, "$push": {"versiones_anteriores": anterior}},
    )
    await base_migracion["alertas"].insert_one(_alr_001_v1())

    await _migrar(ajustes)
    migrada = await base_migracion["alertas"].find_one({"_id": "ALR-001"})

    primera = migrada["reglas_disparadas"][0]
    assert (primera["version"], primera["peso"]) == (1, 35)
