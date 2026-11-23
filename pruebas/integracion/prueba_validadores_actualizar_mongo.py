"""T-24 · `actualizar-validadores.js` contra MongoDB de verdad.

Corre el script tal cual (`mongosh --file`, como `python tareas.py validadores`) sobre
una base desechable. Lo que sostiene:

- un validador viejo se cambia por el nuevo SIN perder un solo documento;
- una base ya al dia no cambia nada y lo dice;
- una coleccion que falta se crea vacia con su validador;
- la base temporal `antifraude_validadores_tmp` queda borrada, siempre.

Necesita `mongosh` en el PATH.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from pymongo.errors import OperationFailure, WriteError

from app.nucleo.config import Ajustes
from app.repos.esquemas import obtener_validadores
from pruebas.conftest import saltar_o_fallar

pytestmark = pytest.mark.mongo

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "semilla" / "actualizar-validadores.js"
SEMILLA_01 = RAIZ / "scripts" / "semilla" / "01-colecciones.js"
BASE_DESTINO = "antifraude_pruebas_validadores"
BASE_TEMPORAL = "antifraude_validadores_tmp"

CLIENTE = {
    "_id": "CLI-001",
    "nombre": "Maria Rodriguez Vargas",
    "cedula": "1-0987-0654",
    "fecha_registro": None,  # se completa en la prueba
    "esquema_version": 1,
}


async def _correr_script(ajustes: Ajustes) -> str:
    mongosh = shutil.which("mongosh")
    if mongosh is None:
        saltar_o_fallar("mongosh no esta en el PATH; el script se corre con mongosh")
    resultado = await asyncio.to_thread(
        subprocess.run,
        [
            mongosh,
            ajustes.mongo.uri,
            "--quiet",
            "--eval",
            f'var BASE_VALIDADORES="{BASE_DESTINO}"; var RUTA_COLECCIONES="{SEMILLA_01}"',
            "--file",
            str(SCRIPT),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    return resultado.stdout


@pytest_asyncio.fixture
async def destino(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    await cliente.drop_database(BASE_DESTINO)
    await cliente.drop_database(BASE_TEMPORAL)
    try:
        yield cliente[BASE_DESTINO]
    finally:
        await cliente.drop_database(BASE_DESTINO)
        await cliente.drop_database(BASE_TEMPORAL)


@pytest.mark.asyncio
async def prueba_cambia_un_validador_viejo_sin_perder_documentos(
    ajustes: Ajustes, destino: Any
) -> None:
    from datetime import UTC, datetime

    cliente = {**CLIENTE, "fecha_registro": datetime(2026, 9, 1, tzinfo=UTC)}

    # 1. Base vacia: el script crea las colecciones con los validadores de 01.
    primera = await _correr_script(ajustes)
    assert "13 creados" in primera, primera
    nuevo = (await obtener_validadores(destino))["clientes"]
    await destino["clientes"].insert_one(cliente)

    # 2. Un validador viejo: rechaza lo que el codigo ya escribe (falta `cedula`).
    viejo = {"$jsonSchema": {"bsonType": "object", "required": ["_id"],
                             "additionalProperties": False,
                             "properties": {"_id": {"bsonType": "string"}}}}
    await destino.command({"collMod": "clientes", "validator": viejo})
    with pytest.raises(WriteError) as rechazo:
        await destino["clientes"].insert_one({**cliente, "_id": "CLI-002"})
    assert rechazo.value.code == 121

    # 3. El script lo actualiza; los documentos siguen ahi y el nuevo ya acepta.
    salida = await _correr_script(ajustes)
    assert "1 actualizados" in salida, salida
    assert "0 creados" in salida, salida
    assert "actualizada          clientes" in salida, salida

    assert (await obtener_validadores(destino))["clientes"] == nuevo
    assert await destino["clientes"].count_documents({}) == 1
    assert (await destino["clientes"].find_one({"_id": "CLI-001"}))["nombre"] == cliente["nombre"]
    await destino["clientes"].insert_one({**cliente, "_id": "CLI-002"})

    # 4. La temporal no queda, y la base destino conserva sus 13 colecciones.
    assert BASE_TEMPORAL not in await destino.client.list_database_names()
    assert len(await destino.list_collection_names()) == 13


@pytest.mark.asyncio
async def prueba_una_base_al_dia_no_cambia_nada(ajustes: Ajustes, destino: Any) -> None:
    await _correr_script(ajustes)
    antes = await obtener_validadores(destino)

    salida = await _correr_script(ajustes)

    assert "0 actualizados, 0 creados, 13 ya al dia" in salida, salida
    assert await obtener_validadores(destino) == antes
    assert BASE_TEMPORAL not in await destino.client.list_database_names()


@pytest.mark.asyncio
async def prueba_una_coleccion_que_falta_se_crea_vacia(
    ajustes: Ajustes, destino: Any
) -> None:
    await _correr_script(ajustes)
    await destino.drop_collection("casos")

    salida = await _correr_script(ajustes)

    assert "1 creados" in salida, salida
    assert "casos" in (await obtener_validadores(destino))
    assert await destino["casos"].count_documents({}) == 0


@pytest.mark.asyncio
async def prueba_el_script_no_modifica_la_semilla_ni_toca_antifraude(
    ajustes: Ajustes, destino: Any, base_mongo: Any
) -> None:
    """Carga 01-colecciones.js contra la temporal: `antifraude` no se escribe.

    Se compara el conteo de documentos de `antifraude` antes y despues (si existe).
    """
    cliente = base_mongo.client

    async def conteos() -> dict[str, int]:
        # Con la autenticacion encendida (H-25) el usuario de pruebas no puede leer
        # `antifraude`: tampoco podria escribirla, y no hay nada que comparar.
        try:
            nombres = await cliente["antifraude"].list_collection_names()
            return {c: await cliente["antifraude"][c].count_documents({}) for c in nombres}
        except OperationFailure as error:
            if error.code != 13:
                raise
            return {}

    antes = await conteos()
    await _correr_script(ajustes)
    assert await conteos() == antes
