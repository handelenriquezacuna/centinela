"""Telefono (390x844, T-74): ninguna pantalla desborda en horizontal y el menu se abre y cierra.

El desborde se mide en el documento: si `scrollWidth` pasa del ancho de la ventana, la
persona tiene que desplazarse de lado para leer la pagina. Las tablas anchas se
desplazan dentro de su propia envoltura, no la pagina.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import ADMINISTRADOR, AGENTE, SUPERVISOR, iniciar_sesion

pytestmark = pytest.mark.e2e

ANCHO, ALTO = 390, 844

PANTALLAS = {
    AGENTE: ["/panel", "/panel?vista=alertas", "/busqueda", "/red-mulas", "/detalle/ALR-001"],
    SUPERVISOR: ["/tablero", "/reglas"],
    ADMINISTRADOR: ["/administracion/salud", "/administracion/respaldos", "/administracion/usuarios"],
}


@pytest.fixture
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_context_args, "viewport": {"width": ANCHO, "height": ALTO}}


def _desborde(page: Page) -> int:
    return int(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"))


# Los elementos que se pasan del borde y no estan dentro de un contenedor que se desplaza solo:
# lo que el mensaje de la prueba nombra cuando una pantalla desborda.
_CULPABLES = """() => {
  const ancho = document.documentElement.clientWidth;
  const encerrado = (e) => {
    for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {
      if (["auto", "scroll", "hidden"].includes(getComputedStyle(a).overflowX)) return true;
    }
    return false;
  };
  return [...document.querySelectorAll("body *")]
    .filter((e) => e.getBoundingClientRect().right > ancho + 1 && !encerrado(e))
    .slice(0, 5)
    .map((e) => e.tagName + "." + String(e.className) + " " + Math.round(e.getBoundingClientRect().right));
}"""


@pytest.mark.parametrize(
    ("agente", "ruta"), [(a, r) for a, rutas in PANTALLAS.items() for r in rutas]
)
def prueba_ninguna_pantalla_desborda_en_horizontal_a_390_px(page: Page, agente: str, ruta: str) -> None:
    iniciar_sesion(page, agente)
    expect(page.get_by_role("button", name="Menú")).to_be_visible()  # la sesion ya quedo
    page.goto(ruta)
    expect(page.locator("main h1").first).to_be_visible()
    page.wait_for_load_state("networkidle")

    desborde = _desborde(page)
    assert desborde <= 0, f"{ruta} se desplaza {desborde} px de lado a {ANCHO} px: {page.evaluate(_CULPABLES)}"


def prueba_el_menu_en_telefono_es_un_boton_que_abre_y_cierra(page: Page) -> None:
    iniciar_sesion(page, SUPERVISOR)
    boton = page.get_by_role("button", name="Menú")
    nav = page.get_by_role("navigation", name="Navegación principal")

    expect(boton).to_be_visible()
    expect(nav).to_be_hidden()
    # Una sola fila de cabecera: el menu cerrado no se come media pantalla.
    assert page.locator("header.encabezado").bounding_box()["height"] < 100  # type: ignore[index]

    boton.click()
    expect(boton).to_have_attribute("aria-expanded", "true")
    expect(nav.get_by_role("link", name="Tablero")).to_be_visible()
    expect(nav.get_by_role("link", name="Reglas y política")).to_be_visible()
    nav.get_by_role("link", name="Búsqueda").click()
    expect(page).to_have_url(re.compile(r"/busqueda$"))
    expect(nav).to_be_hidden()
    expect(boton).to_have_attribute("aria-expanded", "false")

    boton.click()
    page.keyboard.press("Escape")
    expect(nav).to_be_hidden()
