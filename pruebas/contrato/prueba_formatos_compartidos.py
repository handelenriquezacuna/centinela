"""Los formatos y la regla de destino del portal, iguales en Python y en la SPA (T-61).

Los casos viven en `frontend/src/*.casos.json` y los verifican las dos implementaciones:
`app/api/formatos.py` y `destino_seguro` aqui, `frontend/src/formatos.ts` y
`frontend/src/destino.ts` en Vitest. Si alguien cambia una sola de las dos, una de las
dos compuertas falla.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.api.formatos import colones, decimal, etiqueta, hora_costa_rica, porcentaje
from app.api.rutas.acceso import destino_seguro

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
FORMATOS = json.loads((FRONTEND / "formatos.casos.json").read_text(encoding="utf-8"))
DESTINOS = json.loads((FRONTEND / "destino.casos.json").read_text(encoding="utf-8"))


def _fecha(valor: Any) -> Any:
    """La SPA recibe la fecha como texto ISO; Python la tiene como `datetime`."""
    if isinstance(valor, str):
        try:
            return datetime.fromisoformat(valor)
        except ValueError:
            return valor
    return valor


@pytest.mark.parametrize("caso", FORMATOS["colones"], ids=str)
def prueba_colones(caso: dict[str, Any]) -> None:
    assert colones(caso["entrada"]) == caso["salida"]


@pytest.mark.parametrize("caso", FORMATOS["hora_costa_rica"], ids=str)
def prueba_hora_costa_rica(caso: dict[str, Any]) -> None:
    assert hora_costa_rica(_fecha(caso["entrada"])) == caso["salida"]


@pytest.mark.parametrize("caso", FORMATOS["decimal"], ids=str)
def prueba_decimal(caso: dict[str, Any]) -> None:
    assert decimal(caso["entrada"]) == caso["salida"]


@pytest.mark.parametrize("caso", FORMATOS["porcentaje"], ids=str)
def prueba_porcentaje(caso: dict[str, Any]) -> None:
    assert porcentaje(caso["entrada"]) == caso["salida"]


@pytest.mark.parametrize("caso", FORMATOS["etiqueta"], ids=str)
def prueba_etiqueta(caso: dict[str, Any]) -> None:
    assert etiqueta(caso["entrada"]) == caso["salida"]


@pytest.mark.parametrize("caso", DESTINOS["casos"], ids=str)
def prueba_destino_seguro(caso: dict[str, Any]) -> None:
    assert destino_seguro(caso["entrada"]) == caso["salida"]
