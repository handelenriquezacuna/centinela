"""H-18 · El detalle de alerta contra MongoDB de verdad.

Base desechable armada con las colecciones sembradas de `antifraude` (clientes, cuentas,
perfiles, transacciones, agentes, catalogo de reglas, politica y ALR-001), los
validadores reales de `alertas` y `casos`, y la migracion 001 corrida tal cual.
Sostiene:

- el detalle de ALR-001 tras migrar muestra las 3 reglas con su explicacion, la suma 75 y
  el corte CRITICA, el perfil y la transaccion con hora de Costa Rica;
- una transicion completa nueva -> en_revision -> escalada desde la pagina crea el caso
  en la misma transaccion y el historial queda agregado, en orden.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.main import crear_aplicacion
from app.nucleo.config import Ajustes, obtener_ajustes
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from pruebas.conftest import saltar_o_fallar

pytestmark = pytest.mark.mongo

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "migraciones" / "001-alertas-v1-a-v2.js"
BASE_DE_TRABAJO = "antifraude"
BASE_DETALLE = "antifraude_pruebas_detalle"
COPIADAS = (
    "clientes",
    "cuentas",
    "perfiles_comportamiento",
    "transacciones",
    "agentes",
    "reglas_deteccion",
    "politica_deteccion",
)


@pytest_asyncio.fixture
async def base_detalle(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    trabajo = cliente[BASE_DE_TRABAJO]
    validadores = await obtener_validadores(trabajo)
    if not {"alertas", "casos"} <= set(validadores):
        saltar_o_fallar("sin validador de alertas y casos en antifraude; correr 01-colecciones.js")

    await cliente.drop_database(BASE_DETALLE)
    base = cliente[BASE_DETALLE]
    for nombre in ("alertas", "casos"):
        await recrear_con_validador(base, nombre, validadores[nombre])
    for nombre in COPIADAS:
        documentos = await trabajo[nombre].find({}).to_list(length=None)
        if not documentos:
            saltar_o_fallar(f"{BASE_DE_TRABAJO}.{nombre} vacia; correr la siembra")
        await base[nombre].insert_many(documentos)
    alerta = await trabajo["alertas"].find_one({"_id": "ALR-001"})
    if alerta is None or alerta["esquema_version"] != 1:
        saltar_o_fallar("ALR-001 sembrada en v1 no esta en antifraude")
    # Una segunda alerta, sin resolver, para recorrer la maquina de estados.
    nueva = {
        **alerta,
        "_id": "ALR-002",
        "estado": "nueva",
        "historial": [
            {
                "accion": "nueva",
                "agente_id": None,
                "fecha": alerta["fecha_creacion"],
                "nota": "Alerta creada por el motor de deteccion",
            }
        ],
    }
    del nueva["agente_id"]
    await base["alertas"].insert_many([alerta, nueva])
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_DETALLE)


async def _migrar(ajustes: Ajustes) -> None:
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
            f'var BASE_MIGRACION="{BASE_DETALLE}"',
            "--file",
            str(SCRIPT),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stderr + resultado.stdout


@pytest_asyncio.fixture
async def http(ajustes: Ajustes, base_detalle: Any) -> AsyncIterator[AsyncClient]:
    await _migrar(ajustes)
    os.environ["CENTINELA_MONGO__BASE"] = BASE_DETALLE
    obtener_ajustes.cache_clear()
    try:
        async with LifespanManager(crear_aplicacion()) as gestionada, AsyncClient(
            transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
        ) as cliente_http:
            yield cliente_http
    finally:
        os.environ.pop("CENTINELA_MONGO__BASE", None)
        obtener_ajustes.cache_clear()


@pytest.mark.asyncio
async def prueba_detalle_de_alr_001_tras_migrar(http: AsyncClient) -> None:
    respuesta = await http.get("/detalle/ALR-001")
    assert respuesta.status_code == 200
    html = respuesta.text
    assert "aviso-v1" not in html
    for codigo in ("R-01", "R-03", "R-05"):
        assert codigo in html
    assert re.search(r'id="suma-aportes">75<', html)
    assert re.search(r'<tr class="corte-alcanzado">\s*<td>CRITICA</td>', html)
    assert "POL-001" in html
    assert "280 000 CRC" in html and "María Rodríguez Vargas" in html
    assert "2026-09-24 22:47" in html  # 04:47Z es 22:47 del dia anterior en Costa Rica
    assert "45 200 CRC" in html and "07:00 - 19:00" in html
    assert "AGT-001 (José Solís)" in html
    assert re.findall(r"Accion: <strong>(\w+)</strong>", html) == ["confirmada"]
    assert 'id="caso-creado"' not in html  # confirmada, no escalada
    assert 'data-destino=' not in html  # terminal: sin botones


@pytest.mark.asyncio
async def prueba_nueva_en_revision_escalada_crea_el_caso(
    http: AsyncClient, base_detalle: Any
) -> None:
    tomada = await http.post(
        "/detalle/ALR-002/transicion",
        content="destino=en_revision&agente_id=AGT-002&estado_esperado=nueva",
    )
    assert tomada.status_code == 200
    assert 'data-destino="escalada"' in tomada.text

    # Otro agente no la resuelve: 409 visible, sin caso.
    ajeno = await http.post(
        "/detalle/ALR-002/transicion",
        content="destino=escalada&agente_id=AGT-001&estado_esperado=en_revision",
    )
    assert ajeno.status_code == 409 and "AGT-002" in ajeno.text
    assert await base_detalle["casos"].find_one({"_id": "CAS-002"}) is None

    escalada = await http.post(
        "/detalle/ALR-002/transicion",
        content="destino=escalada&agente_id=AGT-002&nota=sospecha+de+mula&estado_esperado=en_revision",
    )
    assert escalada.status_code == 200
    assert "CAS-002" in escalada.text

    caso = await base_detalle["casos"].find_one({"_id": "CAS-002"})
    assert caso is not None and caso["alertas"] == ["ALR-002"]
    alerta = await base_detalle["alertas"].find_one({"_id": "ALR-002"})
    assert alerta["estado"] == "escalada"
    assert [h["accion"] for h in alerta["historial"]] == ["nueva", "en_revision", "escalada"]

    html = (await http.get("/detalle/ALR-002")).text
    assert "CAS-002" in html and "sospecha de mula" in html
    assert 'data-destino=' not in html
