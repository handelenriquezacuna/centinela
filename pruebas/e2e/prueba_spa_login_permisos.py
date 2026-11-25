"""Recorridos 1 y 5 en la SPA (T-67): entrar al portal y que el rol decida las pantallas.

Son los pares de `htmx/prueba_login.py` y `htmx/prueba_permisos.py`, contra el servidor
principal (`base_url`, la SPA). La entrada correcta con cookie esta en
`prueba_spa_panel.py` y la negacion del agente en `prueba_spa_tablero_reglas.py`.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import AGENTE, CONTRASENA, SUPERVISOR, iniciar_sesion

pytestmark = pytest.mark.e2e


def prueba_el_servidor_principal_sirve_la_spa(page: Page) -> None:
    """Guarda del orden: las pruebas de este nivel tienen que ir contra la SPA, no el respaldo."""
    page.goto("/login")
    expect(page.locator("script[src^='/estaticos/spa/']")).to_have_count(1)
    expect(page.locator("#raiz form")).to_have_count(1)
    expect(page.locator("form[action='/login']")).to_have_count(0)


def prueba_credencial_correcta_entra_al_panel_en_la_spa(page: Page) -> None:
    iniciar_sesion(page, AGENTE)

    expect(page).to_have_url(re.compile(r"/panel$"))
    expect(page.get_by_role("heading", name="Panel de alertas")).to_be_visible()
    expect(page.locator("#sesion-quien")).to_contain_text(AGENTE)


def prueba_credencial_incorrecta_muestra_el_error_y_no_entra_en_la_spa(page: Page) -> None:
    iniciar_sesion(page, AGENTE, contrasena="no-es-la-clave-0000")

    expect(page.get_by_role("alert")).to_contain_text("incorrectos")
    expect(page).to_have_url(re.compile(r"/login$"))
    assert not [c for c in page.context.cookies() if c["name"] == "centinela_sesion"]
    # No quedo sesion: el panel manda de vuelta al login.
    page.goto("/panel")
    expect(page).to_have_url(re.compile(r"/login\?siguiente=/panel$"))


def prueba_siguiente_con_otro_sitio_no_saca_al_usuario_de_la_spa(
    page: Page, base_url: str
) -> None:
    page.goto("/login?siguiente=//otro.sitio")
    page.get_by_label("Identificador").fill(AGENTE)
    page.get_by_label(re.compile("Contrase")).fill(CONTRASENA)
    page.get_by_role("button", name="Entrar").click()

    expect(page).to_have_url(re.compile(r"/panel$"))
    assert urlsplit(page.url).netloc == urlsplit(base_url).netloc


def prueba_el_supervisor_entra_al_tablero_en_la_spa(page: Page) -> None:
    iniciar_sesion(page, SUPERVISOR)
    expect(page).to_have_url(re.compile(r"/panel$"))

    respuesta = page.goto("/tablero")

    assert respuesta is not None and respuesta.status == 200
    expect(page.get_by_role("heading", name="Tablero de indicadores")).to_be_visible()
    nav = page.get_by_role("navigation", name="Navegación principal")
    expect(nav.get_by_role("link", name="Tablero")).to_be_visible()
