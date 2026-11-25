"""Detalle de la alerta en la SPA (T-64): tomar y confirmar, con dialogo y con conflicto.

Contra `servidor_e2e` (la SPA). Es el mismo recorrido de `prueba_detalle.py` (HTMX), con el dialogo
accesible de la SPA en lugar del `confirm` del navegador.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.apoyo import entrar_en_spa
from pruebas.e2e.conftest import AGENTE, SUPERVISOR, con_base

pytestmark = pytest.mark.e2e

ALERTA = "ALR-002"  # nueva en el demo, sin agente


async def _estado_y_historial(base: Any) -> tuple[str, str | None, list[str]]:
    alerta = await base["alertas"].find_one({"_id": ALERTA})
    return alerta["estado"], alerta.get("agente_id"), [a["accion"] for a in alerta["historial"]]


async def _otro_agente_la_toma(base: Any) -> None:
    await base["alertas"].update_one(
        {"_id": ALERTA}, {"$set": {"estado": "en_revision", "agente_id": SUPERVISOR}}
    )


def prueba_tomar_y_confirmar_una_alerta_en_la_spa(page: Page, servidor_e2e: str) -> None:
    entrar_en_spa(page, servidor_e2e, AGENTE, f"/detalle/{ALERTA}")
    expect(page.get_by_role("heading", name=ALERTA)).to_be_visible()
    page.evaluate("window.__sinRecargar = true")
    insignia = page.locator("#insignia-estado")
    expect(insignia).to_have_class("insignia insignia-estado es-nueva")

    # Tomar no cierra la alerta: sin dialogo.
    page.locator("button[data-destino='en_revision']").click()
    expect(insignia).to_have_class("insignia insignia-estado es-en_revision")
    assert con_base(_estado_y_historial) == ("en_revision", AGENTE, ["nueva", "en_revision"])
    expect(page.locator("button[data-destino='en_revision']")).to_have_count(0)

    # Confirmar cierra la alerta: pide confirmacion en un dialogo accesible.
    page.get_by_label("Nota").fill("Cliente confirma la llamada")
    page.locator("button[data-destino='confirmada']").click()
    dialogo = page.get_by_role("dialog", name=re.compile("Confirmar fraude"))
    expect(dialogo).to_be_visible()
    assert con_base(_estado_y_historial)[0] == "en_revision"  # todavia no se escribio
    dialogo.get_by_role("button", name="Confirmar fraude").click()

    expect(insignia).to_have_class("insignia insignia-estado es-confirmada")
    expect(page.locator("button[data-destino]")).to_have_count(0)
    expect(page.locator(".historial-item")).to_have_count(3)
    expect(page.locator(".historial")).to_contain_text("Cliente confirma la llamada")
    assert page.evaluate("window.__sinRecargar") is True  # la SPA no recargo la pagina

    estado, agente, acciones = con_base(_estado_y_historial)
    assert (estado, agente) == ("confirmada", AGENTE)
    assert acciones == ["nueva", "en_revision", "confirmada"]


def prueba_cancelar_el_dialogo_no_cierra_la_alerta(page: Page, servidor_e2e: str) -> None:
    entrar_en_spa(page, servidor_e2e, AGENTE, f"/detalle/{ALERTA}")
    page.locator("button[data-destino='en_revision']").click()
    expect(page.locator("#insignia-estado")).to_have_class("insignia insignia-estado es-en_revision")
    page.locator("button[data-destino='descartada']").click()
    dialogo = page.get_by_role("dialog")
    expect(dialogo).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialogo).to_have_count(0)
    assert con_base(_estado_y_historial)[0] == "en_revision"
    # El foco vuelve al boton que abrio el dialogo.
    expect(page.locator("button[data-destino='descartada']")).to_be_focused()


def prueba_si_otro_agente_la_tomo_se_avisa_del_conflicto(page: Page, servidor_e2e: str) -> None:
    entrar_en_spa(page, servidor_e2e, AGENTE, f"/detalle/{ALERTA}")
    expect(page.locator("button[data-destino='en_revision']")).to_be_visible()

    con_base(_otro_agente_la_toma)  # ocurre mientras esta pantalla sigue abierta
    page.locator("button[data-destino='en_revision']").click()

    aviso = page.get_by_role("alert")
    expect(aviso).to_contain_text("otro agente la tomó o resolvió")
    # La pantalla vuelve a leer: muestra el estado vigente y nada de lo que se intento.
    expect(page.locator("#insignia-estado")).to_have_class("insignia insignia-estado es-en_revision")
    expect(page.locator("#agente-asignado")).to_contain_text(SUPERVISOR)
    estado, agente, _ = con_base(_estado_y_historial)
    assert (estado, agente) == ("en_revision", SUPERVISOR)
