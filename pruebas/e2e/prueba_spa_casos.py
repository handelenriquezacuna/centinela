"""Recorrido 9 en la SPA (T-67): la vista por cuenta destino y el lote (T-56).

El par de `htmx/prueba_casos.py`, contra el servidor principal (la SPA): el mismo caso de
tres victimas hacia 6033-9001, la misma confirmacion en un `<dialog>` modal y el mismo
`POST /api/v1/alertas/transiciones`, donde cada alerta pasa sola por la maquina de estados.
La vista por alerta y su recuerdo (localStorage en la SPA, cookie en HTMX) estan en
`prueba_spa_cola_busqueda.py`.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from pruebas.dobles.casos_demo import MULA
from pruebas.e2e.apoyo import (
    IDS_CASO,
    agregar_una_mas,
    casos_de_las_alertas,
    en_revision_de,
    estados_del_caso,
    sembrar_el_caso,
)
from pruebas.e2e.conftest import AGENTE, con_base, iniciar_sesion

pytestmark = pytest.mark.e2e


def _abrir_vista_por_caso(page: Page) -> None:
    """Por omision el panel de la SPA agrupa por cuenta destino (D-56)."""
    iniciar_sesion(page, AGENTE)
    expect(page).to_have_url(re.compile(r"/panel$"))
    expect(page.get_by_role("link", name="Por cuenta destino")).to_have_attribute("aria-current", "page")
    expect(page.locator("#estado-canal")).to_have_text("en vivo")


def prueba_el_caso_de_la_mula_se_pasa_a_en_revision_en_lote(page: Page) -> None:
    con_base(sembrar_el_caso)
    _abrir_vista_por_caso(page)

    caso = page.locator(f"#caso-{MULA}")
    expect(caso).to_be_visible()
    expect(caso.locator(".caso-alertas")).to_have_text("3")
    expect(caso.locator(".caso-victimas")).to_have_text("3")
    expect(caso.locator(".caso-monto")).to_have_text("₡520.000")
    expect(caso.locator(".caso-severidad")).to_have_text("Crítica")
    expect(caso).to_contain_text("En lista de riesgo")
    expect(caso.locator("[data-sugerencia]")).to_contain_text("candidata a confirmar")
    expect(caso.locator("[id^='alerta-']")).to_have_count(3)
    # Nada se cerro solo: las tres siguen nuevas.
    assert {e for e, _ in con_base(estados_del_caso).values()} == {"nueva"}

    caso.get_by_label(re.compile("Seleccionar todas")).check()
    expect(caso.locator(".caso-seleccion")).to_have_text("3 seleccionadas")
    caso.get_by_role("button", name="Tomar").click()

    dialogo = page.get_by_role("dialog", name=re.compile("Tomar"))
    expect(dialogo).to_be_visible()
    expect(dialogo).to_contain_text("3 alertas hacia la cuenta destino 6033-9001")
    dialogo.get_by_label(re.compile("Motivo")).fill("Misma mula, tres víctimas")
    dialogo.get_by_role("button", name="Confirmar").click()

    resultado = caso.locator("[data-resultado-lote]")
    expect(resultado).to_contain_text("Pasaron 3 de 3")
    expect(resultado).to_contain_text("En revisión")
    expect(caso.locator("[id^='alerta-'][data-estado='en_revision']")).to_have_count(3)
    assert con_base(estados_del_caso) == {i: ("en_revision", AGENTE) for i in IDS_CASO}


def prueba_cancelar_el_dialogo_no_cambia_nada(page: Page) -> None:
    con_base(sembrar_el_caso)
    _abrir_vista_por_caso(page)
    caso = page.locator(f"#caso-{MULA}")
    caso.get_by_label("Seleccionar ALR-101").check()
    caso.get_by_role("button", name="Tomar").click()
    dialogo = page.get_by_role("dialog", name=re.compile("Tomar"))
    expect(dialogo).to_be_visible()
    dialogo.get_by_role("button", name="Cancelar").click()
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(caso.locator("[data-resultado-lote]")).to_have_count(0)
    assert {e for e, _ in con_base(estados_del_caso).values()} == {"nueva"}


def prueba_los_atajos_de_teclado_mueven_marcan_y_abren(page: Page) -> None:
    con_base(sembrar_el_caso)
    _abrir_vista_por_caso(page)
    caso = page.locator(f"#caso-{MULA}")
    expect(caso.locator("[id^='alerta-']")).to_have_count(3)
    # Las filas de los atajos son las de todas las cuentas, en orden: se parte de la
    # primera de la mula para no depender de las demas cuentas del demo.
    primera = caso.locator("[id^='alerta-']").first
    segunda = caso.locator("[id^='alerta-']").nth(1)
    primera.evaluate("fila => { fila.tabIndex = -1; fila.focus(); }")

    page.keyboard.press("j")
    expect(segunda).to_be_focused()
    page.keyboard.press("k")
    expect(primera).to_be_focused()

    page.keyboard.press("x")
    expect(primera.get_by_role("checkbox")).to_be_checked()
    expect(caso.locator(".caso-seleccion")).to_have_text("1 seleccionadas")

    page.keyboard.press("?")
    expect(page.get_by_role("heading", name="Atajos de teclado")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("heading", name="Atajos de teclado")).to_be_hidden()

    primera.focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/detalle/ALR-101$"))


def prueba_los_atajos_no_se_disparan_al_escribir(page: Page) -> None:
    con_base(sembrar_el_caso)
    _abrir_vista_por_caso(page)
    caso = page.locator(f"#caso-{MULA}")
    caso.get_by_label("Seleccionar ALR-101").check()
    caso.get_by_role("button", name="Tomar").click()
    motivo = page.get_by_role("dialog").get_by_label(re.compile("Motivo"))
    motivo.press_sequentially("jkx?")
    expect(motivo).to_have_value("jkx?")
    # `?` escrito en el campo no abre la ayuda: sigue un solo dialogo abierto.
    expect(page.get_by_role("dialog")).to_have_count(1)


def prueba_una_alerta_nueva_se_suma_a_su_caso_sin_recargar(page: Page) -> None:
    con_base(sembrar_el_caso)
    _abrir_vista_por_caso(page)
    caso = page.locator(f"#caso-{MULA}")
    expect(caso.locator("[id^='alerta-']")).to_have_count(3)
    caso.get_by_label("Seleccionar ALR-102").check()
    page.evaluate("window.__sinRecargar = true")

    con_base(agregar_una_mas)

    expect(caso.locator("[id^='alerta-']")).to_have_count(4, timeout=15_000)
    expect(caso.locator(".caso-alertas")).to_have_text("4")
    expect(caso.locator(".caso-monto")).to_have_text("₡570.000")
    # Lo que el agente habia marcado se conserva, y la pagina no se recargo.
    expect(caso.get_by_label("Seleccionar ALR-102")).to_be_checked()
    assert page.evaluate("window.__sinRecargar") is True


def prueba_escalar_el_lote_abre_un_solo_caso_de_investigacion(page: Page) -> None:
    con_base(sembrar_el_caso)
    con_base(en_revision_de(AGENTE))
    _abrir_vista_por_caso(page)
    caso = page.locator(f"#caso-{MULA}")
    caso.get_by_label(re.compile("Seleccionar todas")).check()
    caso.get_by_role("button", name="Escalar").click()
    page.get_by_role("dialog").get_by_role("button", name="Confirmar").click()

    resultado = caso.locator("[data-resultado-lote]")
    expect(resultado).to_contain_text("Pasaron 3 de 3")
    expect(resultado).to_contain_text("Caso de investigación abierto: CAS-")
    expect(resultado.get_by_text("Caso de investigación abierto")).to_have_count(1)
    (abierto,) = con_base(casos_de_las_alertas)
    assert sorted(abierto["alertas"]) == IDS_CASO
    assert (abierto["cuenta_destino"], abierto["agente_id"]) == (MULA, AGENTE)
    expect(resultado).to_contain_text(abierto["_id"])
