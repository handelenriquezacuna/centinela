"""H-11 en la SPA: el supervisor ve la notificacion de una alerta CRITICA.

Navegador -> SPA -> `GET /api/v1/notificaciones` -> Mongo, con D2 vivo dentro del `uvicorn`
de la prueba: la alerta se inserta en la base, D2 la ve por el change stream, escribe la
notificacion y manda el correo al SMTP en proceso (`servidor_smtp`). Nada esta
sustituido: lo que el supervisor ve en la pantalla es lo que D2 dejo en `notificaciones`.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from app.modelos import Severidad
from app.repos.carga_demo import a_entero_64
from pruebas.dobles.alertas_d2 import alerta_documento
from pruebas.dobles.servidor_smtp import ServidorSmtp
from pruebas.e2e.apoyo import entrar_en_spa
from pruebas.e2e.conftest import AGENTE, SUPERVISOR, con_base

pytestmark = pytest.mark.e2e


async def _insertar_alerta(base: Any, numero: int, severidad: Severidad, puntaje: int) -> None:
    await base["alertas"].insert_one(
        a_entero_64(alerta_documento(numero, severidad=severidad, puntaje=puntaje))
    )


async def _notificacion(base: Any, alerta_id: str) -> dict[str, Any] | None:
    return await base["notificaciones"].find_one({"alerta_id": alerta_id})


def _esperar_notificacion(alerta_id: str, segundos: int = 20) -> dict[str, Any]:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        encontrada = con_base(lambda base: _notificacion(base, alerta_id))
        if encontrada is not None and encontrada["estado_envio"] != "pendiente":
            return encontrada
        time.sleep(0.2)
    raise AssertionError(f"D2 no dejo la notificacion de {alerta_id} en {segundos} s")


def prueba_el_supervisor_ve_la_notificacion_de_una_alerta_critica(
    page: Page, servidor_e2e: str, servidor_smtp: ServidorSmtp
) -> None:
    # Una ALTA primero: D2 no la notifica. La CRITICA que viene despues es la prueba.
    con_base(lambda base: _insertar_alerta(base, 9101, Severidad.ALTA, 60))
    con_base(lambda base: _insertar_alerta(base, 9102, Severidad.CRITICA, 75))
    nota = _esperar_notificacion("ALR-009102")
    assert nota["estado_envio"] == "enviado"
    assert con_base(lambda base: _notificacion(base, "ALR-009101")) is None

    entrar_en_spa(page, servidor_e2e, SUPERVISOR, "/notificaciones")

    expect(page.get_by_role("heading", name="Notificaciones")).to_be_visible()
    fila = page.get_by_role("row", name="ALR-009102")
    expect(fila).to_be_visible()
    expect(fila).to_contain_text("Maria Rodriguez Vargas")
    expect(fila).to_contain_text("₡280.000")
    expect(fila).to_contain_text("Enviado")
    expect(page.get_by_role("row", name="ALR-009101")).to_have_count(0)
    # El enlace lleva al detalle de la alerta.
    expect(fila.get_by_role("link", name="ALR-009102")).to_have_attribute("href", "/detalle/ALR-009102")
    # Y el correo salio de verdad hacia el SMTP de la prueba.
    asuntos = [str(m["Subject"]) for m in servidor_smtp.mensajes()]
    assert "[Centinela] Alerta crítica ALR-009102: ₡280.000 de Maria Rodriguez Vargas" in asuntos


def prueba_el_menu_del_supervisor_trae_notificaciones(page: Page, servidor_e2e: str) -> None:
    entrar_en_spa(page, servidor_e2e, SUPERVISOR, "/panel")

    page.get_by_role("navigation", name="Navegación principal").get_by_role(
        "link", name="Notificaciones"
    ).click()

    expect(page.get_by_role("heading", name="Notificaciones")).to_be_visible()


def prueba_el_agente_no_ve_el_menu_ni_la_pantalla_de_notificaciones(
    page: Page, servidor_e2e: str
) -> None:
    entrar_en_spa(page, servidor_e2e, AGENTE, "/panel")
    expect(
        page.get_by_role("navigation", name="Navegación principal").get_by_role(
            "link", name="Notificaciones"
        )
    ).to_have_count(0)

    page.goto(f"{servidor_e2e}/notificaciones")

    expect(page.get_by_role("heading", name="Acceso denegado")).to_be_visible()
