"""D-74 contra MongoDB de verdad: `mongodump` y `mongorestore` reales, por la API.

Se levanta la aplicacion completa sobre una base propia, `antifraude_pruebas_respaldos`
(la que se respalda), y su base temporal de verificacion, `..._verificacion` (la que se
crea y se borra). Los respaldos caen en un directorio temporal de la prueba. NO se toca
`antifraude`.

Lo que sostiene:

- crear deja el archivo `.gz` y el manifiesto JSON, con el autor, el tamano y los conteos;
- verificar restaura en la base temporal, los conteos coinciden, y la temporal YA NO EXISTE
  al terminar (mirado en el servidor, no en lo que dice la respuesta);
- un archivo alterado, o un manifiesto cuyos conteos no son los de la base, da `fallida` y la
  temporal se borra igual;
- una operacion a la vez: la segunda es 409;
- todo queda en el log `centinela.auditoria`;
- la restauracion de verdad (`tareas.py restaurar`) esta en `prueba_tareas_restaurar`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.main import crear_aplicacion
from app.modelos import Agente, RolAgente
from app.nucleo.config import obtener_ajustes
from app.repos.carga_demo import cargar_demo
from pruebas.conftest import saltar_o_fallar
from pruebas.dobles.datos_demo import DATOS_DEMO
from pruebas.sesiones import encabezados_de

pytestmark = pytest.mark.mongo

BASE_RESPALDADA = "antifraude_pruebas_respaldos"
BASE_TEMPORAL = f"{BASE_RESPALDADA}_verificacion"
RUTA = "/api/v1/respaldos"


@pytest_asyncio.fixture
async def base_respaldada(base_mongo: Any) -> AsyncIterator[Any]:
    """La base que se respalda, con el demo cargado, y la temporal ausente."""
    if shutil.which("mongodump") is None or shutil.which("mongorestore") is None:
        saltar_o_fallar("mongodump y mongorestore (mongodb-database-tools) no estan en el PATH")
    cliente = base_mongo.client
    await cliente.drop_database(BASE_RESPALDADA)
    await cliente.drop_database(BASE_TEMPORAL)
    base = cliente[BASE_RESPALDADA]
    await cargar_demo(base, DATOS_DEMO)
    await cargar_demo(
        base, {"agentes": [Agente(_id="AGT-900", nombre="Admin", rol=RolAgente.ADMINISTRADOR)]}
    )
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_RESPALDADA)
        await cliente.drop_database(BASE_TEMPORAL)


@pytest_asyncio.fixture
async def cliente_respaldos(
    base_respaldada: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("CENTINELA_MONGO__BASE", BASE_RESPALDADA)
    monkeypatch.setenv("CENTINELA_RESPALDOS__DIRECTORIO", str(tmp_path / "respaldos"))
    obtener_ajustes.cache_clear()
    creada = crear_aplicacion("htmx")
    async with LifespanManager(creada) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app),
        base_url="http://pruebas",
        headers=encabezados_de(RolAgente.ADMINISTRADOR, "AGT-900"),
        timeout=120,
    ) as http:
        yield http


async def _bases(base: Any) -> list[str]:
    return (await base.client.list_database_names())


async def _conteos(base: Any) -> dict[str, int]:
    return {
        nombre: await base[nombre].count_documents({})
        for nombre in sorted(await base.list_collection_names(filter={"type": "collection"}))
        if not nombre.startswith("system.")
    }


@pytest.mark.asyncio
async def prueba_crear_y_verificar_un_respaldo_real_y_la_temporal_se_borra(
    cliente_respaldos: AsyncClient,
    base_respaldada: Any,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="centinela.auditoria")
    esperado = await _conteos(base_respaldada)
    assert esperado["transacciones"] > 0 and esperado["agentes"] > 0

    creado = await cliente_respaldos.post(RUTA)

    assert creado.status_code == 201, creado.text
    manifiesto = creado.json()
    directorio = tmp_path / "respaldos"
    archivo = directorio / manifiesto["archivo"]
    assert archivo.is_file() and archivo.stat().st_size == manifiesto["tamano_bytes"] > 0
    assert (directorio / f"{manifiesto['respaldo_id']}.json").is_file()
    assert manifiesto["base"] == BASE_RESPALDADA
    assert manifiesto["creado_por"] == "AGT-900"
    assert manifiesto["creado_utc"].endswith("Z")
    assert manifiesto["herramienta"].startswith("mongodump version")
    # D1 (su token) y D3 (el indicador del dia) escriben solos mientras corre el dump: esas dos
    # colecciones se miden con una ventana, las demas tienen que coincidir exactamente.
    for coleccion, n in esperado.items():
        if coleccion not in ("deteccion_estado", "indicadores_diarios"):
            assert manifiesto["documentos_por_coleccion"][coleccion] == n
    assert (await cliente_respaldos.get(RUTA)).json()["total"] == 1

    verificado = await cliente_respaldos.post(f"{RUTA}/{manifiesto['respaldo_id']}/verificar")

    assert verificado.status_code == 200, verificado.text
    resultado = verificado.json()["verificacion"]
    assert resultado["resultado"] == "correcta", resultado
    assert resultado["diferencias"] == []
    assert resultado["base_temporal"] == BASE_TEMPORAL
    assert resultado["base_temporal_borrada"] is True
    assert resultado["documentos_restaurados"]["transacciones"] == esperado["transacciones"]
    # La temporal no existe en el servidor: no es lo que dice la respuesta, es lo que hay.
    assert BASE_TEMPORAL not in await _bases(base_respaldada)
    # y la base respaldada sigue intacta
    assert (await _conteos(base_respaldada))["transacciones"] == esperado["transacciones"]
    # el resultado quedo en el manifiesto del disco
    guardado = json.loads((directorio / f"{manifiesto['respaldo_id']}.json").read_text())
    assert guardado["verificacion"]["resultado"] == "correcta"
    mensajes = [r.getMessage() for r in caplog.records if r.name == "centinela.auditoria"]
    assert any("respaldo creado" in m and "AGT-900" in m for m in mensajes)
    assert any("verificacion correcta" in m and "AGT-900" in m for m in mensajes)


@pytest.mark.asyncio
async def prueba_un_manifiesto_con_otros_conteos_da_fallida_y_la_temporal_se_borra(
    cliente_respaldos: AsyncClient, base_respaldada: Any, tmp_path: Path
) -> None:
    creado = (await cliente_respaldos.post(RUTA)).json()
    ruta = tmp_path / "respaldos" / f"{creado['respaldo_id']}.json"
    manifiesto = json.loads(ruta.read_text())
    for clave in ("documentos_por_coleccion", "documentos_al_terminar"):
        manifiesto[clave]["alertas"] += 5  # el respaldo "prometia" mas de lo que tiene
    ruta.write_text(json.dumps(manifiesto))

    respuesta = await cliente_respaldos.post(f"{RUTA}/{creado['respaldo_id']}/verificar")

    assert respuesta.status_code == 200
    resultado = respuesta.json()["verificacion"]
    assert resultado["resultado"] == "fallida"
    assert [d["coleccion"] for d in resultado["diferencias"]] == ["alertas"]
    assert resultado["diferencias"][0]["restaurados"] == manifiesto["documentos_por_coleccion"]["alertas"] - 5
    assert resultado["base_temporal_borrada"] is True
    assert BASE_TEMPORAL not in await _bases(base_respaldada)


@pytest.mark.asyncio
async def prueba_un_archivo_alterado_da_fallida_sin_restaurar(
    cliente_respaldos: AsyncClient, base_respaldada: Any, tmp_path: Path
) -> None:
    creado = (await cliente_respaldos.post(RUTA)).json()
    with (tmp_path / "respaldos" / creado["archivo"]).open("ab") as archivo:
        archivo.write(b"basura")

    resultado = (
        await cliente_respaldos.post(f"{RUTA}/{creado['respaldo_id']}/verificar")
    ).json()["verificacion"]

    assert resultado["resultado"] == "fallida"
    assert "no coincide" in resultado["detalle"]
    assert BASE_TEMPORAL not in await _bases(base_respaldada)


@pytest.mark.asyncio
async def prueba_verificar_dos_veces_deja_el_ultimo_resultado_y_ninguna_temporal(
    cliente_respaldos: AsyncClient, base_respaldada: Any
) -> None:
    creado = (await cliente_respaldos.post(RUTA)).json()

    for _ in range(2):
        respuesta = await cliente_respaldos.post(f"{RUTA}/{creado['respaldo_id']}/verificar")
        assert respuesta.json()["verificacion"]["resultado"] == "correcta"

    assert BASE_TEMPORAL not in await _bases(base_respaldada)


@pytest.mark.asyncio
async def prueba_una_operacion_a_la_vez_la_segunda_es_409(cliente_respaldos: AsyncClient) -> None:
    primera, segunda = await asyncio.gather(
        cliente_respaldos.post(RUTA), cliente_respaldos.post(RUTA)
    )

    assert sorted([primera.status_code, segunda.status_code]) == [201, 409]
    perdedora = primera if primera.status_code == 409 else segunda
    assert perdedora.json()["error"]["codigo"] == "conflicto"
    assert (await cliente_respaldos.get(RUTA)).json()["total"] == 1


@pytest.mark.asyncio
async def prueba_verificar_un_id_inexistente_es_404(cliente_respaldos: AsyncClient) -> None:
    respuesta = await cliente_respaldos.post(f"{RUTA}/RSP-20200101T000000Z/verificar")

    assert respuesta.status_code == 404
    assert respuesta.json()["error"]["codigo"] == "no_encontrado"
