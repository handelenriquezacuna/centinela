"""Sankey en la SPA (T-64): el supervisor marca una mula y el nodo cambia sin recargar."""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.apoyo import entrar_en_spa
from pruebas.e2e.conftest import AGENTE, SUPERVISOR, con_base

pytestmark = pytest.mark.e2e

# La cadena del demo (TXN-005..007): 6033-9001 -> 7104-2288 -> 8455-1177 -> 6001-3344.
MULA = "8455-1177"


async def _entrada_de(base: Any) -> dict[str, Any] | None:
    return await base["listas_riesgo"].find_one({"cuenta": MULA})


def prueba_el_supervisor_marca_una_mula_y_el_nodo_cambia(page: Page, servidor_e2e: str) -> None:
    entrar_en_spa(page, servidor_e2e, SUPERVISOR, "/detalle/ALR-001")
    page.evaluate("window.__sinRecargar = true")

    sankey = page.locator("#sankey-svg")
    expect(sankey).to_be_visible()
    expect(sankey).to_have_attribute("role", "img")
    expect(sankey).to_have_attribute("aria-label", re.compile("desde la víctima .*: 4 transferencias"))
    expect(page.locator("#sankey")).to_have_attribute("data-saltos", "3")
    expect(page.locator("#sankey")).to_have_attribute("data-transferencias", "4")
    victima = sankey.locator("g.sankey-nodo[data-victima='si']")
    expect(victima).to_have_count(1)
    expect(victima).to_have_attribute("data-columna", "0")
    expect(victima).to_have_attribute("data-en-lista", "no")
    expect(victima).to_contain_text("Víctima")
    bandas = sankey.locator("path.sankey-banda")
    expect(bandas).to_have_count(4)
    grosores = [int(bandas.nth(i).get_attribute("data-grosor") or 0) for i in range(4)]
    assert grosores[0] == 140 and grosores[0] > grosores[1] > grosores[2] > grosores[3] > 0
    for monto, banda in zip((280_000, 276_000, 185_000, 90_000), range(4), strict=True):
        assert bandas.nth(banda).get_attribute("data-monto") == str(monto)
    expect(bandas.nth(0).locator("title")).to_have_text(re.compile(r"^₡280\.000 de .* a 6033-9001, \d{2}/\d{2}/\d{4} \d{2}:\d{2} \(hora CR\)$"))

    nodo = sankey.locator(f'g.sankey-nodo[data-cuenta="{MULA}"]')
    expect(nodo).to_have_attribute("data-en-lista", "no")
    expect(sankey.locator('g.sankey-nodo[data-en-lista="si"]')).to_have_count(1)  # 6033-9001

    # Sin motivo no se escribe nada y el servidor ni se entera.
    fila = page.locator(f'#sankey-cuentas li[data-cuenta="{MULA}"]')
    fila.get_by_role("button", name="Marcar como mula").click()
    expect(page.locator("#sankey-mensaje")).to_contain_text("El motivo es obligatorio")
    assert con_base(_entrada_de) is None

    fila.get_by_label(re.compile("Motivo para marcar")).fill("Recibio el dinero de la victima")
    fila.get_by_role("button", name="Marcar como mula").click()
    dialogo = page.get_by_role("dialog", name="Marcar como mula")
    expect(dialogo).to_contain_text(MULA)
    assert con_base(_entrada_de) is None  # confirmar es lo que escribe
    dialogo.get_by_role("button", name="Marcar como mula").click()

    expect(page.locator(f'#sankey-svg g.sankey-nodo[data-cuenta="{MULA}"]')).to_have_attribute(
        "data-en-lista", "si"
    )
    expect(page.locator("#sankey-mensaje")).to_contain_text("quedó en lista de riesgo")
    expect(page.locator(f'#sankey-cuentas li[data-cuenta="{MULA}"]')).to_contain_text("En lista de riesgo")
    expect(page.get_by_role("button", name="Marcar como mula")).to_have_count(2)
    expect(page.locator("#sankey-cuentas li[data-victima='si'] button")).to_have_count(0)
    assert page.evaluate("window.__sinRecargar") is True

    entrada = con_base(_entrada_de)
    assert entrada is not None and entrada["vigente"] is True
    assert entrada["motivo"] == "Recibio el dinero de la victima"


def prueba_el_agente_ve_el_sankey_pero_no_el_boton_de_marcar(page: Page, servidor_e2e: str) -> None:
    entrar_en_spa(page, servidor_e2e, AGENTE, "/red-mulas/6033-9001")

    expect(page.locator("#sankey-svg path.sankey-banda")).to_have_count(3)
    expect(page.locator("#sankey-cuentas")).to_contain_text(MULA)
    expect(page.get_by_role("button", name="Marcar como mula")).to_have_count(0)
    expect(page.get_by_label(re.compile("Motivo para marcar"))).to_have_count(0)
    # La tabla de saltos del Sankey y la de la red son la alternativa textual.
    expect(page.locator("#tabla-aristas tbody tr")).to_have_count(3)
    page.get_by_text("Ver los saltos del recorrido en una tabla").click()
    expect(page.locator("#tabla-saltos-sankey tbody tr")).to_have_count(3)
