"""T-82: el aviso al cliente, de punta a punta con navegador real.

Recorrido: una transferencia de riesgo de Maria (CLI-001, con telefono y consentimiento) -> D1
crea la alerta CRITICA -> D2 manda el SMS simulado -> el supervisor lo ve en el simulador de
telefono y abre el enlace en OTRO contexto del navegador, sin sesion -> el cliente responde
"No fui yo" -> la alerta sube al tope de la cola del supervisor, sin recargar, con la insignia
-> el agente la toma y la confirma -> la pagina del cliente cambia sola de estado.

Con `CENTINELA_CAPTURAS=1` guarda tres capturas a 390x844 en `docs/ux-capturas/cliente-*.png`
(el SMS en el simulador y la pagina del cliente antes y despues de responder).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

from app.repos.carga_demo import a_entero_64
from pruebas.dobles.alertas_d2 import alerta_documento
from pruebas.e2e.apoyo import entrar_en_spa, insertar_transaccion_de_riesgo
from pruebas.e2e.conftest import SUPERVISOR, con_base

pytestmark = pytest.mark.e2e

CAPTURAS = Path(__file__).resolve().parents[2] / "docs" / "ux-capturas"
MOVIL = {"width": 390, "height": 844}


def _capturar(page: Page, nombre: str) -> None:
    if os.environ.get("CENTINELA_CAPTURAS") == "1":
        CAPTURAS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(CAPTURAS / f"{nombre}.png"), full_page=True)


async def _sms(base: Any) -> list[dict[str, Any]]:
    return await base["notificaciones"].find({"canal": "sms"}).to_list(length=None)


def _esperar_sms(segundos: int = 25) -> dict[str, Any]:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        encontrados = [n for n in con_base(_sms) if n["estado_envio"] == "enviado"]
        if encontrados:
            return encontrados[0]
        time.sleep(0.2)
    raise AssertionError(f"D2 no dejo el SMS al cliente en {segundos} s")


def prueba_el_supervisor_abre_el_sms_el_cliente_responde_y_el_agente_lo_ve_arriba(
    page: Page, browser: Browser, servidor_e2e: str
) -> None:
    # Una alerta abierta de puntaje 100 (sin cliente con telefono): la "No fui yo" debe pasarle por encima.
    con_base(lambda base: base["alertas"].insert_one(a_entero_64(alerta_documento(9300, puntaje=100))))
    entrar_en_spa(page, servidor_e2e, SUPERVISOR, "/panel?vista=alertas")
    expect(page.locator("#estado-canal")).to_have_text("en vivo")
    primera_fila = page.locator("#cola tr[id^='alerta-']").first
    expect(primera_fila).to_have_attribute("id", "alerta-ALR-009300")

    # D1 detecta la transferencia de Maria y D2 le manda el SMS.
    con_base(insertar_transaccion_de_riesgo)
    nota = _esperar_sms()
    alerta_id = nota["alerta_id"]
    assert nota["destinatarios"] == ["8***-**55"]
    assert "enlace_hash" in nota and len(nota["enlace_hash"]) == 64

    # El supervisor abre el simulador de telefono (desde Notificaciones) y ve el SMS con su enlace.
    page.goto(f"{servidor_e2e}/notificaciones")
    page.get_by_role("link", name=re.compile("simulador de teléfono", re.IGNORECASE)).click()
    expect(page.get_by_role("heading", name="Simulador de teléfono")).to_be_visible()
    sms = page.locator(f"article.sms[data-alerta='{alerta_id}']")
    expect(sms).to_contain_text("detectamos una transferencia SINPE de ₡600.000")
    expect(sms).to_contain_text("cuenta terminada en 4455")
    expect(sms).not_to_contain_text("María")  # sin datos sensibles
    expect(sms).to_contain_text("Para 8***-**55")
    enlace = sms.get_by_role("link")
    ruta = enlace.get_attribute("href")
    assert ruta is not None and re.fullmatch(r"/c/[A-Za-z0-9_-]{43}", ruta)
    if os.environ.get("CENTINELA_CAPTURAS") == "1":
        movil = browser.new_context(viewport=MOVIL, base_url=servidor_e2e)
        pagina = movil.new_page()
        pagina.goto(f"{servidor_e2e}/login")
        pagina.get_by_label("Identificador").fill(SUPERVISOR)
        from pruebas.e2e.conftest import CONTRASENA

        pagina.get_by_label(re.compile("Contrase")).fill(CONTRASENA)
        pagina.get_by_role("button", name="Entrar").click()
        pagina.goto(f"{servidor_e2e}/simulador-sms")
        expect(pagina.locator(f"article.sms[data-alerta='{alerta_id}']")).to_be_visible()
        _capturar(pagina, "cliente-sms-simulador")
        movil.close()

    # El panel sigue en la pagina de la cola: vuelve a ella para vigilar el cambio en vivo.
    page.goto(f"{servidor_e2e}/panel?vista=alertas")
    expect(page.locator("#estado-canal")).to_have_text("en vivo")
    expect(page.locator("#cola tr[id^='alerta-']").first).to_have_attribute("id", "alerta-ALR-009300")

    # El cliente: otro contexto, SIN sesion, en un telefono.
    contexto = browser.new_context(viewport=MOVIL, base_url=servidor_e2e)
    try:
        cliente = contexto.new_page()
        cliente.goto(f"{servidor_e2e}{ruta}")
        expect(cliente.get_by_role("heading", name="¿Fuiste tú?")).to_be_visible()
        expect(cliente.locator("#cliente-monto")).to_have_text("₡600.000")
        expect(cliente.locator("#cliente")).to_contain_text("terminada en 4455")
        expect(cliente.locator("#cliente")).to_contain_text("7001-****")  # destino enmascarado
        expect(cliente.locator("#cliente")).not_to_contain_text("7001-0010")
        expect(cliente.locator("#cliente")).not_to_contain_text("María")
        assert cliente.get_by_role("link").count() == 0  # nada que lleve al portal del banco
        expect(cliente.locator("#cliente")).to_contain_text("800-000-0000 (número de ejemplo)")
        _capturar(cliente, "cliente-pagina-antes")

        cliente.get_by_role("button", name="No fui yo", exact=True).click()
        cliente.get_by_role("button", name="Sí, no fui yo").click()  # la confirmacion
        expect(cliente.locator("#cliente-caso")).to_contain_text("Recibimos tu respuesta")
        expect(cliente.locator("#cliente-respuesta")).to_contain_text("No fui yo")
        expect(cliente.get_by_role("button", name="No fui yo", exact=True)).to_have_count(0)
        _capturar(cliente, "cliente-pagina-despues")

        # El agente lo ve ARRIBA y sin recargar: el canal reordena la cola.
        expect(page.locator("#cola tr[id^='alerta-']").first).to_have_attribute("id", f"alerta-{alerta_id}")
        expect(page.locator(f"#alerta-{alerta_id}")).to_contain_text("Cliente: no fui yo")
        expect(page.locator(f"#alerta-{alerta_id}")).to_have_attribute("data-cliente", "no_fui_yo")

        # El detalle: insignia, sugerencia, propuesta de mula y telefono enmascarado.
        page.locator(f"#alerta-{alerta_id}").get_by_role("link", name=alerta_id).click()
        expect(page.locator("#insignia-cliente")).to_have_text("Cliente: no fui yo")
        expect(page.locator("#sugerencia-cliente")).to_contain_text("NO fue él")
        expect(page.locator("#propuesta-mula")).to_contain_text("7001-0010")
        expect(page.locator("#cliente-telefono")).to_contain_text("8***-**55")
        expect(page.locator(".historial")).to_contain_text("Respuesta del cliente")
        expect(page.locator("#insignia-estado")).to_have_text("Nueva")  # la alerta NO se cerro sola

        # Ya respondio: el mismo enlace no deja responder otra vez (pagina nueva sin sesion).
        otra = contexto.new_page()
        otra.goto(f"{servidor_e2e}{ruta}")
        expect(otra.locator("#cliente-caso")).to_contain_text("Recibimos tu respuesta")
        expect(otra.get_by_role("button", name="No fui yo", exact=True)).to_have_count(0)
        otra.close()

        # El agente decide y el cliente ve cambiar su estado SOLO (sondeo de la pagina).
        page.get_by_role("button", name="Tomar").click()
        expect(cliente.locator("#cliente-caso")).to_contain_text("Un agente revisa tu caso", timeout=15_000)
        page.get_by_role("button", name="Confirmar fraude").click()
        page.get_by_role("button", name="Confirmar fraude").last.click()
        expect(page.locator("#insignia-estado")).to_have_text("Confirmada")
        expect(cliente.locator("#cliente-caso")).to_contain_text(
            "Confirmamos el fraude: llama al 800-000-0000", timeout=15_000
        )
    finally:
        contexto.close()


def prueba_un_enlace_inexistente_o_vencido_muestra_la_pagina_con_el_numero_de_ayuda(
    browser: Browser, servidor_e2e: str
) -> None:
    contexto = browser.new_context(viewport=MOVIL, base_url=servidor_e2e)
    try:
        pagina = contexto.new_page()
        pagina.goto(f"{servidor_e2e}/c/{'Q' * 43}")
        expect(pagina.get_by_role("heading", name="Este enlace ya no sirve")).to_be_visible()
        expect(pagina.locator("#cliente-no-valido")).to_contain_text("800-000-0000 (número de ejemplo)")
        assert pagina.get_by_role("link").count() == 0
    finally:
        contexto.close()


def prueba_sin_sesion_el_simulador_manda_al_login_y_el_agente_no_lo_abre(
    page: Page, servidor_e2e: str
) -> None:
    from pruebas.e2e.conftest import AGENTE

    page.goto(f"{servidor_e2e}/simulador-sms")
    expect(page).to_have_url(re.compile(r"/login\?siguiente=/simulador-sms$"))
    entrar_en_spa(page, servidor_e2e, AGENTE, "/simulador-sms")
    expect(page.get_by_role("heading", name="Simulador de teléfono")).to_have_count(0)
