"""Apoyo de las e2e de la SPA (fase 2): entrar por el formulario de la SPA, no por el HTMX.

`page.goto("/ruta")` resuelve contra el servidor HTMX (`base_url`); las pruebas de la SPA
van contra `servidor_spa`, asi que reciben la URL completa y navegan con ella.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import CONTRASENA


def entrar_en_spa(page: Page, url: str, agente_id: str, destino: str) -> None:
    """Abre `destino` en la SPA, entra con el formulario y vuelve a donde iba."""
    page.goto(f"{url}{destino}")
    expect(page).to_have_url(re.compile(r"/login\?siguiente="))
    page.get_by_label("Identificador").fill(agente_id)
    page.get_by_label(re.compile("Contrase")).fill(CONTRASENA)
    page.get_by_role("button", name="Entrar").click()
    expect(page).to_have_url(re.compile(re.escape(destino) + "$"))
