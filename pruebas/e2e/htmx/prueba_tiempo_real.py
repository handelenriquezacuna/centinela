"""Recorrido 2: una transaccion de riesgo llega al panel abierto, sin recargar (SSE)."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.apoyo import DESTINO_NUEVO, insertar_transaccion_de_riesgo
from pruebas.e2e.conftest import AGENTE, con_base, iniciar_sesion

pytestmark = pytest.mark.e2e


def prueba_la_alerta_aparece_sola_en_el_panel_abierto(page: Page) -> None:
    """Vista por alerta, elegida a proposito: por omision el panel agrupa por cuenta destino."""
    iniciar_sesion(page, AGENTE)
    expect(page).to_have_url(re.compile(r"/panel$"))
    page.get_by_role("link", name="Por alerta").click()
    expect(page.locator("#cola")).to_be_attached()
    # El canal tiene que estar abierto ANTES de insertar: el flujo no repite lo anterior.
    expect(page.locator("#estado-canal")).to_have_text("en vivo")
    filas = page.locator("[id^='alerta-']")
    antes = filas.count()
    nueva = filas.filter(has_text=DESTINO_NUEVO)
    expect(nueva).to_have_count(0)

    con_base(insertar_transaccion_de_riesgo)

    # D1 detecta, escribe la alerta, el change stream la empuja y HTMX la pinta: sin recargar.
    expect(nueva).to_have_count(1, timeout=20_000)
    expect(filas).to_have_count(antes + 1)
    expect(nueva).to_contain_text("Maria Rodriguez Vargas")
    assert nueva.get_attribute("data-estado") == "nueva"
    assert nueva.get_attribute("data-monto-crc") == "600000"


def prueba_la_alerta_aparece_sola_en_la_vista_por_cuenta_destino(page: Page) -> None:
    """La vista por omision: la fila llega por SSE y crea (o engorda) su cuenta destino."""
    iniciar_sesion(page, AGENTE)
    expect(page).to_have_url(re.compile(r"/panel$"))
    expect(page.locator("#casos")).to_be_attached()
    expect(page.locator("#estado-canal")).to_have_text("en vivo")
    cuenta = page.locator(f"#caso-{DESTINO_NUEVO}")
    expect(cuenta).to_have_count(0)

    con_base(insertar_transaccion_de_riesgo)

    expect(cuenta).to_have_count(1, timeout=20_000)
    fila = cuenta.locator("[id^='alerta-']")
    expect(fila).to_have_count(1)
    expect(fila).to_contain_text("Maria Rodriguez Vargas")
    assert fila.get_attribute("data-estado") == "nueva"
    assert fila.get_attribute("data-monto-crc") == "600000"
    assert fila.get_attribute("data-destino") == DESTINO_NUEVO
    expect(cuenta.locator(".caso-monto")).to_have_text("₡600.000")
