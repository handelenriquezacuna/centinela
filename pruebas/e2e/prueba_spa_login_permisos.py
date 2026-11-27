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

from pruebas.e2e.conftest import (
    ADMINISTRADOR,
    AGENTE,
    CONTRASENA,
    SUPERVISOR,
    iniciar_sesion,
)

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
    expect(page.locator("#sesion-quien")).to_contain_text("Agente E2E (agente)")


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
    expect(page).to_have_url(re.compile(r"/tablero$"))

    respuesta = page.goto("/tablero")

    assert respuesta is not None and respuesta.status == 200
    expect(page.get_by_role("heading", name="Tablero de indicadores")).to_be_visible()
    nav = page.get_by_role("navigation", name="Navegación principal")
    expect(nav.get_by_role("link", name="Tablero")).to_be_visible()


# ---------------------------------------------------------------------------
# Cada rol aterriza en su pantalla y ve su menu (T-74)
# ---------------------------------------------------------------------------

ENLACES = {
    "agente": ["Panel", "Búsqueda", "Red de mulas"],
    "supervisor": ["Panel", "Búsqueda", "Red de mulas", "Tablero", "Reglas y política", "Notificaciones"],
    "administrador": [
        "Panel",
        "Búsqueda",
        "Red de mulas",
        "Tablero",
        "Reglas y política",
        "Notificaciones",
        "Salud del sistema",
        "Respaldos",
        "Usuarios del portal",
        "Bitácora de auditoría",
    ],
}


@pytest.mark.parametrize(
    ("agente", "rol", "inicio", "titulo"),
    [
        (AGENTE, "agente", r"/panel$", "Panel de alertas"),
        (SUPERVISOR, "supervisor", r"/tablero$", "Tablero de indicadores"),
        (ADMINISTRADOR, "administrador", r"/administracion/salud$", "Salud del sistema"),
    ],
)
def prueba_cada_rol_aterriza_en_su_inicio_con_su_menu(
    page: Page, agente: str, rol: str, inicio: str, titulo: str
) -> None:
    iniciar_sesion(page, agente)

    expect(page).to_have_url(re.compile(inicio))
    expect(page.get_by_role("heading", level=1, name=titulo)).to_be_visible()
    nav = page.get_by_role("navigation", name="Navegación principal")
    expect(nav.get_by_role("link")).to_have_text(ENLACES[rol])
    # La raiz y el logotipo llevan al mismo sitio.
    page.goto("/")
    expect(page).to_have_url(re.compile(inicio))
    page.get_by_role("link", name=re.compile("Centinela")).click()
    expect(page).to_have_url(re.compile(inicio))


def prueba_el_supervisor_no_entra_a_administracion_y_la_pantalla_dice_quien_si(page: Page) -> None:
    iniciar_sesion(page, SUPERVISOR)
    expect(page).to_have_url(re.compile(r"/tablero$"))  # la sesion ya quedo antes de navegar

    respuesta = page.goto("/administracion/respaldos")

    assert respuesta is not None and respuesta.status == 403
    expect(page.get_by_role("heading", name="Acceso denegado")).to_be_visible()
    expect(page.get_by_text("Esta pantalla es para el administrador.")).to_be_visible()
    page.get_by_role("link", name="Ir a mi pantalla de inicio").click()
    expect(page).to_have_url(re.compile(r"/tablero$"))


def prueba_el_administrador_ve_salud_y_las_entradas_de_administracion(page: Page) -> None:
    iniciar_sesion(page, ADMINISTRADOR)

    expect(page.get_by_text("Conectada")).to_be_visible()
    expect(page.locator("#salud-replica")).to_contain_text("rsfraude")
    tabla = page.get_by_role("table", name=re.compile("Tareas de fondo"))
    expect(tabla.get_by_text("D1 · Detección")).to_be_visible()
    nav = page.get_by_role("navigation", name="Navegación principal")
    nav.get_by_role("link", name="Respaldos").click()
    expect(page.get_by_role("heading", name="Respaldos", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Crear respaldo")).to_be_visible()
    nav.get_by_role("link", name="Usuarios del portal").click()
    expect(page.get_by_text("python tareas.py usuarios --help")).to_be_visible()


def prueba_una_direccion_desconocida_con_sesion_muestra_la_pagina_404_del_portal(page: Page) -> None:
    iniciar_sesion(page, AGENTE)
    expect(page).to_have_url(re.compile(r"/panel$"))

    respuesta = page.goto("/respaldos")

    assert respuesta is not None and respuesta.status == 404
    expect(page.get_by_role("heading", name="Página no encontrada")).to_be_visible()
    expect(page.get_by_role("navigation", name="Navegación principal")).to_be_visible()
