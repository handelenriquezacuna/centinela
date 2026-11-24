"""Recorrido de referencia de la SPA (T-61): entrar y ver llegar una alerta sola al panel.

Contra `servidor_spa` (`portal.interfaz: spa`): navegador -> index.html de la SPA -> API
JSON -> Mongo, y el canal SSE JSON empujando la alerta que escribe D1. Es la pantalla
modelo para la fase 2: los selectores son por rol, etiqueta accesible o `data-*`, igual
que en las e2e HTMX, para que la misma prueba sirva cuando la SPA pase a ser la unica.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import AGENTE, CONTRASENA, con_base
from pruebas.e2e.prueba_tiempo_real import (
    DESTINO_NUEVO,
    _insertar_transaccion_de_riesgo,
)

pytestmark = pytest.mark.e2e


def prueba_la_spa_entra_con_cookie_y_la_alerta_llega_sola_por_el_canal_json(
    page: Page, servidor_spa: str
) -> None:
    # Sin sesion, el servidor manda al login conservando el destino.
    page.goto(f"{servidor_spa}/panel")
    expect(page).to_have_url(re.compile(r"/login\?siguiente=/panel$"))
    expect(page.get_by_role("heading", name="Iniciar sesión")).to_be_visible()

    page.get_by_label("Identificador").fill(AGENTE)
    page.get_by_label(re.compile("Contrase")).fill(CONTRASENA)
    page.get_by_role("button", name="Entrar").click()

    # Vuelve a donde iba, sin recargar: es la SPA la que navega.
    expect(page).to_have_url(re.compile(r"/panel$"))
    expect(page.get_by_role("heading", name="Panel de alertas")).to_be_visible()
    expect(page.locator("#sesion-quien")).to_contain_text(AGENTE)

    # La sesion es la cookie HttpOnly; la SPA no guarda el token en ningun lado.
    cookie = next(c for c in page.context.cookies() if c["name"] == "centinela_sesion")
    assert cookie["httpOnly"] is True and cookie["sameSite"] == "Strict"
    almacenado = page.evaluate("JSON.stringify([Object.keys(localStorage), Object.keys(sessionStorage)])")
    assert almacenado == "[[],[]]"

    # El canal tiene que estar abierto ANTES de insertar: el flujo no repite lo anterior.
    expect(page.locator("#estado-canal")).to_have_text("en vivo")
    cuenta = page.locator(f"#caso-{DESTINO_NUEVO}")
    expect(cuenta).to_have_count(0)
    page.evaluate("window.__sinRecargar = true")

    con_base(_insertar_transaccion_de_riesgo)

    # D1 detecta y escribe, el change stream la empuja como JSON y la SPA pide su caso.
    expect(cuenta).to_have_count(1, timeout=20_000)
    fila = cuenta.locator("tr[id^='alerta-']")
    expect(fila).to_have_count(1)
    expect(fila).to_contain_text("Maria Rodriguez Vargas")
    assert fila.get_attribute("data-estado") == "nueva"
    assert fila.get_attribute("data-monto-crc") == "600000"
    assert fila.get_attribute("data-destino") == DESTINO_NUEVO
    expect(cuenta.locator(".caso-monto")).to_have_text("₡600.000")
    expect(fila.locator(".alerta-fecha")).to_have_text(re.compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$"))
    expect(page.locator("#aviso-nuevas")).to_contain_text(DESTINO_NUEVO)
    assert page.evaluate("window.__sinRecargar") is True

    # Las casillas y las acciones tienen nombre accesible.
    expect(cuenta.get_by_role("checkbox", name=re.compile("Seleccionar todas"))).to_be_visible()
    expect(cuenta.get_by_role("button", name="Tomar")).to_be_visible()
