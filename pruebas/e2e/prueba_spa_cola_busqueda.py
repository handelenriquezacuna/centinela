"""Recorridos de la SPA para el panel "Por alerta", los atajos, la busqueda y la sesion (T-65).

Contra `servidor_spa` (`portal.interfaz: spa`), igual que `prueba_spa_panel.py`. Selectores
por rol, etiqueta accesible o `data-*`, los mismos que usan las e2e HTMX.
"""

from __future__ import annotations

import csv
import io
import re

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.conftest import AGENTE, CONTRASENA, con_base
from pruebas.e2e.prueba_tiempo_real import (
    DESTINO_NUEVO,
    _insertar_transaccion_de_riesgo,
)

pytestmark = pytest.mark.e2e


def _entrar(page: Page, base: str, ruta: str = "/panel") -> None:
    page.goto(f"{base}{ruta}")
    page.get_by_label("Identificador").fill(AGENTE)
    page.get_by_label(re.compile("Contrase")).fill(CONTRASENA)
    page.get_by_role("button", name="Entrar").click()
    expect(page).to_have_url(re.compile(re.escape(ruta) + "$"))


def prueba_la_vista_por_alerta_recibe_la_fila_sola_y_la_ordena_por_puntaje(
    page: Page, servidor_spa: str
) -> None:
    _entrar(page, servidor_spa)
    page.get_by_role("link", name="Por alerta").click()
    expect(page).to_have_url(re.compile(r"/panel\?vista=alertas$"))
    expect(page.get_by_role("link", name="Por alerta")).to_have_attribute("aria-current", "page")
    expect(page.locator("#cola")).to_be_attached()
    expect(page.locator("#estado-canal")).to_have_text("en vivo")

    filas = page.locator("#cola [role='row']")
    antes = filas.count()
    assert antes > 0
    nueva = filas.filter(has_text=DESTINO_NUEVO)
    expect(nueva).to_have_count(0)
    page.evaluate("window.__sinRecargar = true")

    con_base(_insertar_transaccion_de_riesgo)

    # D1 detecta, el change stream empuja la alerta como JSON y la SPA la inserta.
    expect(nueva).to_have_count(1, timeout=20_000)
    expect(filas).to_have_count(antes + 1)
    expect(nueva).to_contain_text("Maria Rodriguez Vargas")
    expect(nueva).to_contain_text("₡600.000")
    assert nueva.get_attribute("data-estado") == "nueva"
    assert nueva.get_attribute("data-monto-crc") == "600000"
    assert page.evaluate("window.__sinRecargar") is True
    # El orden es el del servidor: puntaje descendente.
    puntajes = [int(f.get_attribute("data-puntaje") or "0") for f in filas.all()]
    assert puntajes == sorted(puntajes, reverse=True)
    expect(page.locator("#aviso-nuevas")).to_contain_text("Alerta nueva o actualizada")

    # La eleccion de vista se recuerda: /panel sin parametro vuelve a la cola por alerta.
    page.goto(f"{servidor_spa}/panel")
    expect(page.locator("#cola")).to_be_attached()


def prueba_atajos_j_k_enter_y_ayuda_en_el_panel(page: Page, servidor_spa: str) -> None:
    _entrar(page, servidor_spa, "/panel?vista=alertas")
    filas = page.locator("#cola [role='row']")
    expect(filas.first).to_be_visible()
    primera = filas.first.get_attribute("id")
    segunda = filas.nth(1).get_attribute("id")

    page.keyboard.press("j")
    assert page.evaluate("document.activeElement.id") == primera
    page.keyboard.press("j")
    assert page.evaluate("document.activeElement.id") == segunda
    page.keyboard.press("k")
    assert page.evaluate("document.activeElement.id") == primera
    # Foco visible: contorno del atajo.
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"

    page.keyboard.press("?")
    expect(page.get_by_role("heading", name="Atajos de teclado")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("heading", name="Atajos de teclado")).to_be_hidden()

    page.keyboard.press("Enter")
    alerta_id = (primera or "").removeprefix("alerta-")
    expect(page).to_have_url(re.compile(rf"/detalle/{alerta_id}$"))


def prueba_los_atajos_se_ignoran_al_escribir_en_la_busqueda(page: Page, servidor_spa: str) -> None:
    _entrar(page, servidor_spa, "/busqueda")
    cuenta = page.get_by_label(re.compile("Cuenta"))
    cuenta.fill("")
    cuenta.press_sequentially("jk?x")
    expect(cuenta).to_have_value("jk?x")


def prueba_la_busqueda_filtra_pagina_y_exporta_el_csv_con_los_mismos_filtros(
    page: Page, servidor_spa: str
) -> None:
    _entrar(page, servidor_spa, "/busqueda")
    expect(page.get_by_role("heading", name="Búsqueda forense")).to_be_visible()
    expect(page.get_by_role("table")).to_be_visible()

    page.get_by_label("Cuenta (origen o destino)").fill("CTA-001")
    page.get_by_label(re.compile("Monto máximo")).fill("300000")
    page.get_by_role("button", name="Buscar").click()
    expect(page).to_have_url(re.compile(r"cuenta=CTA-001"))
    fila = page.locator("tbody tr[data-transaccion='TXN-001']")
    expect(fila).to_have_count(1)
    expect(fila).to_contain_text("₡280.000")
    expect(fila.locator("td.hora")).to_have_text(re.compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$"))
    expect(page.locator("#total-resultados")).to_have_text("1 resultados")
    # La tabla tiene nombre y encabezados para un lector de pantalla.
    expect(page.get_by_role("table", name="Transacciones que cumplen los filtros")).to_be_visible()
    expect(page.get_by_role("columnheader", name="Monto")).to_be_visible()

    with page.expect_download() as descarga:
        page.get_by_role("link", name="Exportar CSV").click()
    contenido = open(descarga.value.path(), encoding="utf-8").read()  # noqa: SIM115
    filas = list(csv.reader(io.StringIO(contenido)))
    assert filas[0][:3] == ["transaccion_id", "fecha_hora_cr", "cuenta_origen"]
    assert [f[0] for f in filas[1:]] == ["TXN-001"]
    assert filas[1][5] == "280000"  # entero, sin formato

    # Un filtro sin coincidencias no ofrece exportar.
    page.get_by_label("Cuenta (origen o destino)").fill("CTA-404")
    page.get_by_role("button", name="Buscar").click()
    expect(page.get_by_text("No hay transacciones que cumplan los filtros.")).to_be_visible()
    expect(page.get_by_role("link", name="Exportar CSV")).to_have_count(0)


def prueba_la_busqueda_avisa_de_un_monto_con_decimales_sin_pedir_nada(
    page: Page, servidor_spa: str
) -> None:
    _entrar(page, servidor_spa, "/busqueda?monto_max=9.5")
    expect(page.get_by_role("alert")).to_contain_text("debe ser un entero en colones")


def prueba_cerrar_sesion_la_borra_y_la_navegacion_sigue_el_rol(page: Page, servidor_spa: str) -> None:
    _entrar(page, servidor_spa)
    nav = page.get_by_role("navigation", name="Navegación principal")
    expect(nav.get_by_role("link", name="Panel")).to_be_visible()
    expect(nav.get_by_role("link", name="Búsqueda")).to_be_visible()
    # Un agente no ve el tablero y, si lo pide, la SPA dice acceso denegado.
    expect(nav.get_by_role("link", name="Tablero")).to_have_count(0)
    page.goto(f"{servidor_spa}/tablero")
    expect(page.get_by_role("heading", name="Acceso denegado")).to_be_visible()

    page.get_by_role("button", name="Salir").click()
    expect(page).to_have_url(re.compile(r"/login$"))
    assert not [c for c in page.context.cookies() if c["name"] == "centinela_sesion"]
    page.goto(f"{servidor_spa}/panel")
    expect(page).to_have_url(re.compile(r"/login\?siguiente=/panel$"))


def prueba_una_ruta_que_no_existe_muestra_la_pagina_404_propia(page: Page, servidor_spa: str) -> None:
    _entrar(page, servidor_spa)
    page.get_by_role("link", name="Búsqueda").click()
    page.evaluate("history.pushState({}, '', '/no-existe'); dispatchEvent(new PopStateEvent('popstate'))")
    expect(page.get_by_role("heading", name="Página no encontrada")).to_be_visible()
    expect(page.get_by_role("link", name="Volver al panel")).to_be_visible()
