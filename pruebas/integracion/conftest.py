"""Fixtures de las pruebas de integracion que comparten varios archivos."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio

from pruebas.integracion.apoyo_d2 import base_d2_desechable


@pytest_asyncio.fixture
async def base_d2(base_mongo: Any) -> AsyncIterator[Any]:
    """Base `antifraude_pruebas_d2`: validadores e indices reales de la semilla, sin datos."""
    async for base in base_d2_desechable(base_mongo):
        yield base
