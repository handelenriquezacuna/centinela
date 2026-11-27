"""T-83 en la SPA: bloqueo por intentos fallidos y aviso de sesion por vencer."""

from __future__ import annotations

import re
import time

import pytest
from playwright.sync_api import Page, expect

from app.modelos import Agente, RolAgente
from app.repos.agentes import RepoAgentes
from app.repos.carga_demo import cargar_demo
from app.seguridad.usuarios import asignar_credencial
from pruebas.e2e.conftest import AGENTE, CONTRASENA, con_base, iniciar_sesion

pytestmark = pytest.mark.e2e

MENSAJE = "Identificador o contraseña incorrectos, o demasiados intentos"
BLOQUEABLE = "AGT-904"


async def _crear_bloqueable(base: object) -> None:
    await base["agentes"].delete_one({"_id": BLOQUEABLE})  # type: ignore[index]
    await cargar_demo(
        base, {"agentes": [Agente(_id=BLOQUEABLE, nombre="Bloqueable E2E", rol=RolAgente.AGENTE)]}
    )
    await asignar_credencial(RepoAgentes(base), BLOQUEABLE, CONTRASENA)  # type: ignore[arg-type]


def prueba_cinco_fallos_bloquean_y_la_clave_buena_ya_no_entra(page: Page) -> None:
    con_base(_crear_bloqueable)

    for _ in range(5):
        iniciar_sesion(page, BLOQUEABLE, contrasena="no-es-la-clave-0000")
        expect(page.get_by_role("alert")).to_contain_text(MENSAJE)

    # La sexta, con la clave BUENA, falla igual y con el mismo texto.
    iniciar_sesion(page, BLOQUEABLE)
    expect(page.get_by_role("alert")).to_contain_text(MENSAJE)
    expect(page).to_have_url(re.compile(r"/login$"))
    assert not [c for c in page.context.cookies() if c["name"] == "centinela_sesion"]

    # Las demas cuentas no se ven afectadas.
    iniciar_sesion(page, AGENTE)
    expect(page).to_have_url(re.compile(r"/panel$"))


def prueba_el_mensaje_no_delata_si_la_cuenta_existe(page: Page) -> None:
    iniciar_sesion(page, "AGT-999", contrasena="no-es-la-clave-0000")
    inexistente = page.get_by_role("alert").inner_text()
    iniciar_sesion(page, AGENTE, contrasena="no-es-la-clave-0000")
    existente = page.get_by_role("alert").inner_text()

    assert inexistente == existente and MENSAJE in existente


def prueba_el_aviso_de_sesion_aparece_a_un_minuto_y_seguir_trabajando_renueva(
    page: Page, servidor_sesion_corta: str
) -> None:
    """La sesion del servidor dura 2 minutos. El reloj del NAVEGADOR se adelanta con
    `page.clock` (el servidor sigue en hora real): no se espera un minuto de verdad."""
    page.clock.install()
    page.goto(f"{servidor_sesion_corta}/login")
    page.get_by_label("Identificador").fill(AGENTE)
    page.get_by_label(re.compile("Contrase")).fill(CONTRASENA)
    page.get_by_role("button", name="Entrar").click()
    expect(page).to_have_url(re.compile(r"/panel$"))
    antes = page.evaluate("fetch('/api/v1/sesion').then(r => r.json()).then(s => s.expira)")
    aviso = page.get_by_role("alertdialog", name="Tu sesión vence en 1 minuto")
    expect(aviso).not_to_be_visible()

    page.clock.fast_forward(65_000)  # quedan 55 s de los 120

    expect(aviso).to_be_visible()
    expect(aviso.get_by_role("button", name="Seguir trabajando")).to_be_focused()
    expect(aviso.get_by_role("button", name="Salir")).to_be_visible()

    # El token lleva la expiracion en segundos enteros: sin esperar uno, la nueva seria igual.
    time.sleep(1.2)
    with page.expect_response(re.compile(r"/api/v1/sesion/renovar$")) as respuesta:
        aviso.get_by_role("button", name="Seguir trabajando").click()

    assert respuesta.value.status == 200
    despues = page.evaluate("fetch('/api/v1/sesion').then(r => r.json()).then(s => s.expira)")
    assert despues > antes  # nueva expiracion, ISO en UTC: el orden de texto es el del tiempo
    expect(page).to_have_url(re.compile(r"/panel$"))
    # La cookie sigue siendo HttpOnly y SameSite=Strict.
    cookie = next(c for c in page.context.cookies() if c["name"] == "centinela_sesion")
    assert cookie["httpOnly"] and cookie["sameSite"] == "Strict"
