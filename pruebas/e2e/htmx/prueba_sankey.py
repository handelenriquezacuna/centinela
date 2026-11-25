"""Recorrido 5 (T-55): el supervisor sigue el dinero del caso en el Sankey y marca una mula."""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import AGENTE, SUPERVISOR, con_base, iniciar_sesion

pytestmark = pytest.mark.e2e

# La cadena del demo (TXN-005..007): 6033-9001 -> 7104-2288 -> 8455-1177 -> 6001-3344.
# D-55: el demo solo trae a 6033-9001 en listas_riesgo; las otras tres son propuestas de la red.
MULA = "8455-1177"


async def _entrada_de(base: Any) -> dict[str, Any] | None:
    return await base["listas_riesgo"].find_one({"cuenta": MULA})


def prueba_el_supervisor_sigue_el_dinero_del_caso_y_marca_una_mula(page: Page) -> None:
    iniciar_sesion(page, SUPERVISOR)
    page.goto("/detalle/ALR-001")  # el caso: TXN-001, 280.000 hacia 6033-9001

    sankey = page.locator("#sankey-svg")
    expect(sankey).to_be_visible()
    expect(sankey).to_have_attribute("role", "img")
    expect(sankey).to_have_attribute("aria-label", re.compile("desde la víctima .*: 4 transferencias"))
    expect(page.locator("#sankey")).to_have_attribute("data-saltos", "3")
    expect(page.locator("#sankey")).to_have_attribute("data-transferencias", "4")
    # Columna 0: la víctima, con su texto y sin botón ni trama.
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

    nodo = sankey.locator(f'g.sankey-nodo[data-cuenta="{MULA}"]')
    expect(nodo).to_have_attribute("data-en-lista", "no")
    expect(sankey.locator('g.sankey-nodo[data-en-lista="si"]')).to_have_count(1)  # 6033-9001

    fila = page.locator(f'#sankey-cuentas li[data-cuenta="{MULA}"]')
    fila.get_by_label(re.compile("Motivo para marcar")).fill("Recibio el dinero de la victima")
    page.once("dialog", lambda dialogo: dialogo.accept())
    fila.get_by_role("button", name="Marcar como mula").click()

    expect(page.locator(f'#sankey-svg g.sankey-nodo[data-cuenta="{MULA}"]')).to_have_attribute(
        "data-en-lista", "si"
    )
    expect(page.locator("#sankey-mensaje")).to_contain_text("quedó en lista de riesgo")
    expect(page.locator(f'#sankey-cuentas li[data-cuenta="{MULA}"]')).to_contain_text(
        "En lista de riesgo"
    )
    # Quedan 2 propuestas con botón (7104-2288 y 6001-3344); la víctima nunca lo tiene.
    expect(page.get_by_role("button", name="Marcar como mula")).to_have_count(2)
    expect(page.locator("#sankey-cuentas li[data-victima='si'] button")).to_have_count(0)

    entrada = con_base(_entrada_de)
    assert entrada is not None and entrada["vigente"] is True
    assert entrada["motivo"] == "Recibio el dinero de la victima"


def prueba_el_agente_ve_el_sankey_pero_no_el_boton_de_marcar(page: Page) -> None:
    iniciar_sesion(page, AGENTE)
    page.goto("/red-mulas/6033-9001")

    expect(page.locator("#sankey-svg path.sankey-banda")).to_have_count(3)
    expect(page.locator("#sankey-cuentas")).to_contain_text(MULA)
    expect(page.get_by_role("button", name="Marcar como mula")).to_have_count(0)
    # La tabla de saltos sigue como alternativa textual.
    expect(page.locator("#tabla-aristas tbody tr")).to_have_count(3)
