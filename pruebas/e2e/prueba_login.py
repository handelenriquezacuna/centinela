"""Recorrido 1: entrar al portal. Credencial buena, credencial mala y redireccion abierta."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import AGENTE, CONTRASENA, iniciar_sesion

pytestmark = pytest.mark.e2e


def prueba_credencial_correcta_entra_al_panel(page: Page) -> None:
    iniciar_sesion(page, AGENTE)

    expect(page).to_have_url(re.compile(r"/panel$"))
    expect(page.get_by_role("heading", name="Panel de alertas")).to_be_visible()
    expect(page.locator("#sesion-quien")).to_contain_text(AGENTE)


def prueba_credencial_incorrecta_muestra_el_error_y_no_entra(page: Page) -> None:
    iniciar_sesion(page, AGENTE, contrasena="no-es-la-clave-0000")

    expect(page).to_have_url(re.compile(r"/login$"))
    expect(page.get_by_role("alert")).to_contain_text("incorrectos")
    # No quedo sesion: el panel manda de vuelta a /login.
    page.goto("/panel")
    expect(page).to_have_url(re.compile(r"/login"))


def prueba_siguiente_con_otro_sitio_no_saca_al_usuario_del_portal(
    page: Page, base_url: str
) -> None:
    page.goto("/login?siguiente=//otro.sitio")
    page.get_by_label("Identificador").fill(AGENTE)
    page.get_by_label(re.compile("Contrase")).fill(CONTRASENA)
    page.get_by_role("button", name="Entrar").click()

    expect(page).to_have_url(re.compile(r"/panel$"))
    assert urlsplit(page.url).netloc == urlsplit(base_url).netloc
