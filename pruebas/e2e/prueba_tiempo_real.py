"""Recorrido 2: una transaccion de riesgo llega al panel abierto, sin recargar (SSE)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from app.modelos import ZONA_COSTA_RICA, Transaccion
from app.repos.carga_demo import a_entero_64
from pruebas.e2e.conftest import AGENTE, con_base, iniciar_sesion

pytestmark = pytest.mark.e2e

DESTINO_NUEVO = "7001-0010"


async def _insertar_transaccion_de_riesgo(base: Any) -> None:
    """Transferencia nocturna grande de Maria a un destino nuevo: R-01 + R-03 + R-05."""
    transaccion = Transaccion(
        _id="TXN-9001",
        cuenta_origen="CTA-001",
        cuenta_destino=DESTINO_NUEVO,
        monto_crc=600_000,
        fecha=datetime(2026, 9, 26, 23, 15, tzinfo=ZONA_COSTA_RICA),
    )
    await base["transacciones"].insert_one(a_entero_64(transaccion.a_documento()))


def prueba_la_alerta_aparece_sola_en_el_panel_abierto(page: Page) -> None:
    iniciar_sesion(page, AGENTE)
    expect(page).to_have_url(re.compile(r"/panel$"))
    # El canal tiene que estar abierto ANTES de insertar: el flujo no repite lo anterior.
    expect(page.locator("#estado-canal")).to_have_text("en vivo")
    filas = page.locator("[id^='alerta-']")
    antes = filas.count()
    nueva = filas.filter(has_text=DESTINO_NUEVO)
    expect(nueva).to_have_count(0)

    con_base(_insertar_transaccion_de_riesgo)

    # D1 detecta, escribe la alerta, el change stream la empuja y HTMX la pinta: sin recargar.
    expect(nueva).to_have_count(1, timeout=20_000)
    expect(filas).to_have_count(antes + 1)
    expect(nueva).to_contain_text("Maria Rodriguez Vargas")
    assert nueva.get_attribute("data-estado") == "nueva"
    assert nueva.get_attribute("data-monto-crc") == "600000"
