"""T-84 en la SPA: el administrador filtra la bitacora por un agente y ve sus transiciones.

Contra `servidor_e2e` (la SPA): el agente toma y confirma una alerta con la pantalla de detalle
(la escritura real, con su historial), y despues el administrador abre
`/administracion/bitacora`, filtra por ese agente y ve exactamente esas dos acciones, con su
nota, y las exporta a CSV. Nada esta sustituido: la bitacora lee lo que dejo la transicion.
"""

from __future__ import annotations

import csv
import io
import re

import pytest
from playwright.sync_api import Page, expect

from pruebas.e2e.apoyo import entrar_en_spa
from pruebas.e2e.conftest import ADMINISTRADOR, AGENTE, SUPERVISOR, iniciar_sesion

pytestmark = pytest.mark.e2e

ALERTA = "ALR-002"  # nueva en el demo, sin agente
NOTA = "Cliente confirma la llamada"


def _el_agente_toma_y_confirma(page: Page, servidor: str) -> None:
    entrar_en_spa(page, servidor, AGENTE, f"/detalle/{ALERTA}")
    page.locator("button[data-destino='en_revision']").click()
    expect(page.locator("#insignia-estado")).to_have_class("insignia insignia-estado es-en_revision")
    page.get_by_label("Nota").fill(NOTA)
    page.locator("button[data-destino='confirmada']").click()
    page.get_by_role("dialog", name=re.compile("Confirmar fraude")).get_by_role(
        "button", name="Confirmar fraude"
    ).click()
    expect(page.locator("#insignia-estado")).to_have_class("insignia insignia-estado es-confirmada")


def prueba_el_administrador_filtra_por_un_agente_y_ve_sus_transiciones(
    page: Page, servidor_e2e: str
) -> None:
    _el_agente_toma_y_confirma(page, servidor_e2e)

    # Se cambia de sesion: el administrador.
    page.context.clear_cookies()
    entrar_en_spa(page, servidor_e2e, ADMINISTRADOR, "/administracion/bitacora")
    expect(page.get_by_role("heading", name="Bitácora de auditoría")).to_be_visible()
    tabla = page.get_by_role("table", name="Bitácora de auditoría")
    expect(tabla).to_be_visible()
    # Sin filtro hay mas actores que el agente de la prueba (el demo trae los suyos).
    expect(page.locator("#bitacora-total")).to_contain_text("registros")

    page.get_by_label("Agente").fill(AGENTE)
    page.get_by_role("button", name="Buscar").click()
    expect(page).to_have_url(re.compile(rf"actor={AGENTE}"))

    filas = tabla.locator("tbody tr")
    expect(filas).to_have_count(2)
    expect(page.locator("#bitacora-total")).to_have_text("2 registros")
    for i in range(2):
        expect(filas.nth(i)).to_contain_text(AGENTE)
        expect(filas.nth(i)).to_contain_text(ALERTA)
    # La mas reciente primero: confirmar, y despues tomar.
    expect(filas.nth(0)).to_contain_text("Alerta confirmada")
    expect(filas.nth(0)).to_contain_text(f"en_revision -> confirmada: {NOTA}")
    expect(filas.nth(1)).to_contain_text("Alerta tomada")
    expect(filas.nth(1)).to_contain_text("nueva -> en_revision")
    expect(filas.nth(0).get_by_role("link", name=ALERTA)).to_have_attribute("href", f"/detalle/{ALERTA}")

    # El CSV aplica los mismos filtros.
    with page.expect_download() as descarga:
        page.get_by_role("link", name="Exportar CSV").click()
    contenido = open(descarga.value.path(), encoding="utf-8").read()  # noqa: SIM115
    lineas = list(csv.reader(io.StringIO(contenido)))
    assert lineas[0] == ["fecha_hora_cr", "actor", "accion", "objeto", "detalle", "origen"]
    assert [(f[1], f[2], f[3]) for f in lineas[1:]] == [
        (AGENTE, "alerta_confirmada", ALERTA),
        (AGENTE, "alerta_en_revision", ALERTA),
    ]

    # Filtrar por un tipo de accion que el agente no hizo no deja nada.
    page.get_by_label("Tipo de acción").select_option("alerta_escalada")
    page.get_by_role("button", name="Buscar").click()
    expect(page.get_by_text("No hay registros con esos filtros")).to_be_visible()

    # Limpiar vuelve a todo.
    page.get_by_role("button", name="Limpiar").click()
    expect(page.locator("#bitacora-total")).not_to_have_text("2 registros")


def prueba_la_bitacora_solo_se_ve_como_administrador(page: Page, servidor_e2e: str) -> None:
    iniciar_sesion(page, SUPERVISOR)
    expect(page).to_have_url(re.compile(r"/tablero$"))
    nav = page.get_by_role("navigation", name="Navegación principal")
    expect(nav.get_by_role("link", name="Tablero")).to_be_visible()
    expect(nav.get_by_role("link", name="Bitácora de auditoría")).to_have_count(0)

    respuesta = page.goto("/administracion/bitacora")

    assert respuesta is not None and respuesta.status == 403
    expect(page.get_by_role("heading", name="Acceso denegado")).to_be_visible()
    expect(page.get_by_role("heading", name="Bitácora de auditoría")).to_have_count(0)
    assert page.evaluate("async () => (await fetch('/api/v1/bitacora')).status") == 403
    assert page.evaluate("async () => (await fetch('/api/v1/bitacora/exportar.csv')).status") == 403

    page.context.clear_cookies()
    iniciar_sesion(page, ADMINISTRADOR)
    page.get_by_role("navigation", name="Navegación principal").get_by_role(
        "link", name="Bitácora de auditoría"
    ).click()
    expect(page).to_have_url(re.compile(r"/administracion/bitacora$"))
