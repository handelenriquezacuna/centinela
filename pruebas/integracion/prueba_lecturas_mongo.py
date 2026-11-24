"""T-61 · Las lecturas JSON del portal y el canal JSON contra MongoDB de verdad.

Base de pruebas (`antifraude_pruebas`, nunca `antifraude`) con el demo cargado. Se afirma:

1. `GET /api/v1/casos-destino` y `GET /api/v1/alertas/{id}/detalle` recorren el camino
   completo ruta -> dependencia -> repositorio -> Mongo y devuelven dinero entero;
2. el canal JSON (`eventos_json` sobre `RepoFlujoAlertas`, el change stream real) entrega
   una alerta insertada a mano como un evento `alerta` con la forma de la API, y al
   reanudar con su `id` no la repite.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.canal_json import eventos_json
from app.repos.carga_demo import a_entero_64
from app.repos.flujo_alertas import RepoFlujoAlertas
from pruebas.dobles.casos_demo import alerta_de_caso

pytestmark = pytest.mark.mongo


@pytest.mark.asyncio
async def prueba_los_casos_por_destino_salen_de_mongo_con_dinero_entero(
    cliente_con_mongo: AsyncClient,
) -> None:
    respuesta = await cliente_con_mongo.get("/api/v1/casos-destino")
    assert respuesta.status_code == 200, respuesta.text
    pagina = respuesta.json()

    assert pagina["total"] >= 1
    for caso in pagina["elementos"]:
        assert isinstance(caso["monto_total_crc"], int)
        assert all(a["estado"] in ("nueva", "en_revision") for a in caso["alertas"])
    cuenta = pagina["elementos"][0]["cuenta_destino"]
    una = await cliente_con_mongo.get(f"/api/v1/casos-destino/{cuenta}")
    assert una.status_code == 200 and una.json()["cuenta_destino"] == cuenta


@pytest.mark.asyncio
async def prueba_el_detalle_sale_de_mongo_con_su_contexto(cliente_con_mongo: AsyncClient) -> None:
    respuesta = await cliente_con_mongo.get("/api/v1/alertas/ALR-001/detalle")
    assert respuesta.status_code == 200, respuesta.text
    detalle = respuesta.json()

    assert detalle["alerta"]["_id"] == "ALR-001"
    assert detalle["transaccion"]["transaccion_id"] == detalle["alerta"]["transaccion_id"]
    assert isinstance(detalle["transaccion"]["monto_crc"], int)
    assert "simulacion" not in detalle["transaccion"]
    assert detalle["cliente"]["nombre"]


async def _primer_evento(base: Any, *, desde: str | None) -> tuple[Any, list[Any]]:
    """Abre el canal JSON y devuelve (generador, eventos hasta la primera alerta)."""
    flujo = eventos_json(RepoFlujoAlertas(base), id_ultimo_evento=desde, reintento_ms=3000)
    vistos = [await anext(flujo)]  # apertura
    return flujo, vistos


@pytest.mark.asyncio
async def prueba_el_canal_json_entrega_lo_persistido_y_reanuda_sin_repetir(
    base_con_demo: Any,
) -> None:
    coleccion = base_con_demo["alertas"]
    documentos = [alerta_de_caso(n, cliente="Cliente Canal") for n in (901, 902)]
    await coleccion.delete_many({"_id": {"$in": [d["_id"] for d in documentos]}})

    flujo, vistos = await _primer_evento(base_con_demo, desde=None)
    siguiente = asyncio.ensure_future(anext(flujo))
    await asyncio.sleep(0.5)  # el change stream ya esta abierto: watch() se hizo en anext
    await coleccion.insert_one(a_entero_64(documentos[0]))
    evento = await asyncio.wait_for(siguiente, timeout=10)
    await flujo.aclose()

    assert vistos[0].retry == 3000
    assert evento.event == "alerta" and evento.id
    cuerpo = json.loads(evento.data)
    assert cuerpo["formato"] == 1 and cuerpo["operacion"] == "insert"
    assert cuerpo["alerta"]["_id"] == "ALR-901"
    assert cuerpo["alerta"]["monto_crc"] == documentos[0]["monto_crc"]

    # Reanudar con el id recibido: lo siguiente es la segunda alerta, no la primera.
    await coleccion.insert_one(a_entero_64(documentos[1]))
    reanudado, _ = await _primer_evento(base_con_demo, desde=evento.id)
    segundo = await asyncio.wait_for(anext(reanudado), timeout=10)
    await reanudado.aclose()
    assert json.loads(segundo.data)["alerta"]["_id"] == "ALR-902"

    await coleccion.delete_many({"_id": {"$in": [d["_id"] for d in documentos]}})
