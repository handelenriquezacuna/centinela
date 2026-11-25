"""Red de mulas en la SPA (T-64): el grafo, la tabla de saltos, aprobar y dar de baja."""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.apoyo import entrar_en_spa
from pruebas.e2e.conftest import AGENTE, SUPERVISOR, con_base

pytestmark = pytest.mark.e2e

MONTOS = (r"276\D?000", r"185\D?000", r"90\D?000")


async def _entradas(base: Any) -> dict[str, bool]:
    return {e["cuenta"]: e["vigente"] async for e in base["listas_riesgo"].find({})}


def prueba_la_red_de_6033_9001_dibuja_tres_saltos(page: Page, servidor_e2e: str) -> None:
    entrar_en_spa(page, servidor_e2e, AGENTE, "/red-mulas/6033-9001")

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
    # El agente ve las propuestas pero no puede aprobarlas.
    expect(page.locator("#propuestas li")).to_have_count(3)
    expect(page.get_by_role("button", name=re.compile("Agregar .* a lista de riesgo"))).to_have_count(0)
    expect(page.locator("#entradas-lista")).to_have_count(0)


def prueba_el_formulario_lleva_a_la_red_de_la_cuenta(page: Page, servidor_e2e: str) -> None:
    entrar_en_spa(page, servidor_e2e, AGENTE, "/red-mulas")
    page.get_by_label(re.compile("Cuenta")).fill("6033-9001")
    page.get_by_role("button", name="Ver red").click()
    expect(page).to_have_url(re.compile(r"/red-mulas/6033-9001$"))
    expect(page.locator("#grafo-red")).to_be_visible()


def prueba_el_supervisor_aprueba_una_propuesta_y_da_de_baja_una_entrada(
    page: Page, servidor_e2e: str
) -> None:
    entrar_en_spa(page, servidor_e2e, SUPERVISOR, "/red-mulas/6033-9001")
    expect(page.locator("#propuestas li")).to_have_count(3)
    expect(page.locator("#entradas-lista li")).to_have_count(1)  # RSK-001 en 6033-9001

    page.get_by_role("button", name="Agregar 7104-2288 a lista de riesgo").click()
    expect(page.locator("#red-aviso")).to_contain_text("7104-2288 se agregó a la lista de riesgo")
    expect(page.locator("#propuestas li")).to_have_count(2)
    expect(page.locator("#entradas-lista li")).to_have_count(2)
    assert con_base(_entradas) == {"6033-9001": True, "7104-2288": True}

    # Dar de baja pide confirmacion: cancelar no borra, confirmar si.
    boton = page.get_by_role("button", name="Dar de baja RSK-001 de 6033-9001")
    boton.click()
    dialogo = page.get_by_role("dialog", name="Dar de baja")
    dialogo.get_by_role("button", name="Cancelar").click()
    expect(dialogo).to_have_count(0)
    assert "6033-9001" in con_base(_entradas)

    boton.click()
    page.get_by_role("dialog", name="Dar de baja").get_by_role("button", name="Dar de baja").click()
    expect(page.locator("#red-aviso")).to_contain_text("Dada de baja: RSK-001")
    expect(page.locator("#entrada-RSK-001")).to_have_count(0)
    assert "6033-9001" not in con_base(_entradas)
