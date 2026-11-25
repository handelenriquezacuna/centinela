"""Recorrido 3: abrir una alerta, tomarla y confirmarla; el estado se ve y queda en la base."""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import AGENTE, con_base, iniciar_sesion

pytestmark = pytest.mark.e2e

ALERTA = "ALR-002"  # nueva en el demo, sin agente


async def _estado_y_historial(base: Any) -> tuple[str, str | None, list[str]]:
    alerta = await base["alertas"].find_one({"_id": ALERTA})
    return alerta["estado"], alerta.get("agente_id"), [a["accion"] for a in alerta["historial"]]


def prueba_tomar_y_confirmar_una_alerta(page: Page) -> None:
    # La confirmacion es terminal: si la pagina pide confirmarla en un dialogo, se acepta.
    page.on("dialog", lambda dialogo: dialogo.accept())
    iniciar_sesion(page, AGENTE)
    page.goto(f"/detalle/{ALERTA}")
    expect(page.get_by_role("heading", name=ALERTA)).to_be_visible()
    insignia = page.locator("#insignia-estado")
    expect(insignia).to_have_class("insignia insignia-estado es-nueva")

    page.locator("button[data-destino='en_revision']").click()
    expect(insignia).to_have_class("insignia insignia-estado es-en_revision")
    assert con_base(_estado_y_historial) == ("en_revision", AGENTE, ["nueva", "en_revision"])
    # Una alerta en revision ya no se puede tomar de nuevo.
    expect(page.locator("button[data-destino='en_revision']")).to_have_count(0)

    page.get_by_label("Nota").fill("Cliente confirma la llamada")
    page.locator("button[data-destino='confirmada']").click()
    expect(insignia).to_have_class("insignia insignia-estado es-confirmada")
    expect(page.locator("button[data-destino]")).to_have_count(0)
    expect(page.locator(".historial-item")).to_have_count(3)
    expect(page.locator(".historial")).to_contain_text("Cliente confirma la llamada")

    estado, agente, acciones = con_base(_estado_y_historial)
    assert (estado, agente) == ("confirmada", AGENTE)
    assert acciones == ["nueva", "en_revision", "confirmada"]
