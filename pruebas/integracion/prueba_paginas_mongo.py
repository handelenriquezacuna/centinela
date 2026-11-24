"""El panel y las reglas contra Mongo de verdad, con la base sembrada."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.mongo


@pytest.mark.asyncio
async def prueba_get_panel_con_la_base_sembrada(cliente_con_mongo: AsyncClient) -> None:
    respuesta = await cliente_con_mongo.get("/panel?vista=alertas")
    assert respuesta.status_code == 200
    ids = re.findall(r'id="alerta-(ALR-\d+)"', respuesta.text)
    assert sorted(ids) == ["ALR-001", "ALR-002", "ALR-003"]
    assert ids[0] == "ALR-001"  # la critica va primero
    assert "₡280.000" in respuesta.text


@pytest.mark.asyncio
async def prueba_get_reglas_con_la_base_sembrada(cliente_con_mongo: AsyncClient) -> None:
    respuesta = await cliente_con_mongo.get("/reglas")
    assert respuesta.status_code == 200
    assert "R-01" in respuesta.text and "Política vigente" in respuesta.text
