"""Recorrido 4: la red de mulas del caso documentado dibuja tres saltos."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import AGENTE, iniciar_sesion

pytestmark = pytest.mark.e2e

# El separador de miles cambia con el formato del portal (276 000 o 276.000).
MONTOS = (r"276\D?000", r"185\D?000", r"90\D?000")


def prueba_la_red_de_6033_9001_dibuja_tres_saltos(page: Page) -> None:
    iniciar_sesion(page, AGENTE)
    page.goto("/red-mulas/6033-9001")

    expect(page.get_by_role("heading", name="Red de cuentas mula")).to_be_visible()
    expect(page.locator("#red-resumen")).to_contain_text("6033-9001")
    grafo = page.locator("#grafo-red")
    expect(grafo).to_be_visible()
    expect(grafo.locator("line.arista")).to_have_count(3)
    expect(grafo.locator("g.nodo-grupo")).to_have_count(4)

    filas = page.locator("#tabla-aristas tbody tr")
    expect(filas).to_have_count(3)
    for fila, monto in zip(range(3), MONTOS, strict=True):
        expect(filas.nth(fila)).to_contain_text(re.compile(monto))
    expect(filas.nth(0)).to_contain_text("7104-2288")
    expect(filas.nth(2)).to_contain_text("6001-3344")
