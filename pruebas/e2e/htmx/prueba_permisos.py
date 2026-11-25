"""Recorrido 5: el rol decide a que pantallas se entra."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import AGENTE, SUPERVISOR, iniciar_sesion

pytestmark = pytest.mark.e2e


def prueba_el_agente_no_entra_al_tablero(page: Page) -> None:
    iniciar_sesion(page, AGENTE)

    respuesta = page.goto("/tablero")

    assert respuesta is not None and respuesta.status == 403
    expect(page.get_by_role("heading", name="Tablero de indicadores")).to_have_count(0)
    # Tampoco se le ofrece el enlace en la navegacion.
    expect(page.get_by_role("navigation").get_by_role("link", name="Tablero")).to_have_count(0)


def prueba_el_supervisor_entra_al_tablero(page: Page) -> None:
    iniciar_sesion(page, SUPERVISOR)

    respuesta = page.goto("/tablero")

    assert respuesta is not None and respuesta.status == 200
    expect(page).to_have_url(re.compile(r"/tablero"))
    expect(page.get_by_role("navigation").get_by_role("link", name="Tablero")).to_be_visible()
