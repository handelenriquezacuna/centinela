"""H-20 y T-35 contra MongoDB de verdad: el tablero y el grafo sobre la base sembrada."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.mongo


@pytest.mark.asyncio
async def prueba_get_tablero_con_la_base_de_demo(cliente_con_mongo: AsyncClient) -> None:
    """Con o sin rollup materializado, la pagina responde y dice en que estado esta."""
    respuesta = await cliente_con_mongo.get("/tablero")
    assert respuesta.status_code == 200
    assert "Tablero de indicadores" in respuesta.text
    assert ("indicadores_diarios" in respuesta.text and "esta vacia" in respuesta.text) or (
        'id="marca-calculo"' in respuesta.text
    )


@pytest.mark.asyncio
async def prueba_tablero_lee_el_rollup_de_la_coleccion(
    cliente_con_mongo: AsyncClient, base_con_demo: object
) -> None:
    base = base_con_demo  # type: ignore[assignment]
    # D3 arranca con el lifespan de esta app y escribe el dia de hoy; si lo hace despues
    # del delete_many, el tablero se ancla en hoy y no en estos dias fijos. Se espera su
    # primer latido en /estado (el documento de hoy puede venir de otra prueba): la
    # vuelta siguiente llega tras indicadores.intervalo_segundos.
    for _ in range(100):
        tareas = (await cliente_con_mongo.get("/estado")).json()["tareas"]
        d3 = next(t for t in tareas if t["nombre"] == "d3_indicadores")
        if d3["eventos_procesados"] >= 1:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("D3 no completo su primera vuelta en 5 s")
    await base["indicadores_diarios"].delete_many({})  # type: ignore[index]
    await base["indicadores_diarios"].insert_many(  # type: ignore[index]
        [
            {
                "_id": f"2026-11-1{dia}",
                "fecha": f"2026-11-1{dia}",
                "total_alertas": total,
                "por_severidad": {"MEDIA": 0, "ALTA": 0, "CRITICA": total},
                "por_estado": {"nueva": total, "en_revision": 0, "confirmada": 0,
                               "descartada": 0, "escalada": 0},
                "esquema_version": 1,
            }
            for dia, total in ((7, 2), (8, 3))
        ]
    )
    html = (await cliente_con_mongo.get("/tablero?dias=1")).text
    assert 'id="total-actual">3<' in html and 'id="total-previo">2<' in html


@pytest.mark.asyncio
async def prueba_get_red_mulas_con_la_base_de_demo(cliente_con_mongo: AsyncClient) -> None:
    formulario = await cliente_con_mongo.get("/red-mulas")
    assert formulario.status_code == 200
    # 6033-9001 es el destino de ALR-001: sin salidas en el demo, la pagina lo dice.
    respuesta = await cliente_con_mongo.get("/red-mulas/6033-9001")
    assert respuesta.status_code == 200
    assert "Propuestas para listas_riesgo" in respuesta.text
    inexistente = await cliente_con_mongo.get("/red-mulas/CTA-999")
    assert inexistente.status_code == 404
