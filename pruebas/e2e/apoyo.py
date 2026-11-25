"""Apoyo compartido de las e2e: la SPA (por omision, T-67) y el respaldo HTMX (`htmx/`).

Aqui vive lo que usan las dos interfaces, para que un recorrido HTMX y su par de la SPA
siembren exactamente lo mismo: la transaccion de riesgo que D1 convierte en alerta y el
caso de tres victimas hacia la mula 6033-9001.

`page.goto("/ruta")` resuelve contra `base_url`: el servidor de la SPA en este directorio
y el de HTMX en `htmx/` (su conftest lo cambia). Las pruebas de la SPA que reciben
`servidor_e2e` navegan con la URL completa; es el mismo servidor.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from playwright.sync_api import Page, expect

from app.modelos import ZONA_COSTA_RICA, Transaccion
from app.repos.carga_demo import a_entero_64
from pruebas.dobles.casos_demo import alerta_de_caso
from pruebas.e2e.conftest import CONTRASENA

# ---------------------------------------------------------------------------
# Tiempo real: una transaccion de riesgo que D1 convierte en alerta
# ---------------------------------------------------------------------------

DESTINO_NUEVO = "7001-0010"


async def insertar_transaccion_de_riesgo(base: Any) -> None:
    """Transferencia nocturna grande de Maria a un destino nuevo: R-01 + R-03 + R-05."""
    transaccion = Transaccion(
        _id="TXN-9001",
        cuenta_origen="CTA-001",
        cuenta_destino=DESTINO_NUEVO,
        monto_crc=600_000,
        fecha=datetime(2026, 9, 26, 23, 15, tzinfo=ZONA_COSTA_RICA),
    )
    await base["transacciones"].insert_one(a_entero_64(transaccion.a_documento()))


# ---------------------------------------------------------------------------
# Lote por cuenta destino: tres victimas hacia la mula (T-56)
# ---------------------------------------------------------------------------

IDS_CASO = ["ALR-101", "ALR-102", "ALR-103"]


async def sembrar_el_caso(base: Any) -> None:
    """Tres victimas distintas hacia 6033-9001 (ya en listas_riesgo en el demo)."""
    documentos = [
        alerta_de_caso(101, minuto=0, monto_crc=280_000),
        alerta_de_caso(102, minuto=5, puntaje=60, monto_crc=150_000),
        alerta_de_caso(103, minuto=12, puntaje=60, monto_crc=90_000),
    ]
    await base["alertas"].insert_many([a_entero_64(d) for d in documentos])


async def agregar_una_mas(base: Any) -> None:
    await base["alertas"].insert_one(
        a_entero_64(alerta_de_caso(104, minuto=14, puntaje=60, monto_crc=50_000))
    )


async def estados_del_caso(base: Any) -> dict[str, tuple[str, str | None]]:
    documentos = await base["alertas"].find({"_id": {"$in": IDS_CASO}}).to_list(length=None)
    return {d["_id"]: (d["estado"], d.get("agente_id")) for d in documentos}


def en_revision_de(agente_id: str) -> Callable[[Any], Awaitable[None]]:
    """Las tres alertas del caso, ya tomadas por `agente_id` (para escalar en lote)."""

    async def _aplicar(base: Any) -> None:
        await base["alertas"].update_many(
            {"_id": {"$in": IDS_CASO}},
            {"$set": {"estado": "en_revision", "agente_id": agente_id}},
        )

    return _aplicar


async def casos_de_las_alertas(base: Any) -> list[dict[str, Any]]:
    return await base["casos"].find({"alertas": {"$in": IDS_CASO}}).to_list(length=None)


# ---------------------------------------------------------------------------
# Entrar en la SPA
# ---------------------------------------------------------------------------


def entrar_en_spa(page: Page, url: str, agente_id: str, destino: str) -> None:
    """Abre `destino` en la SPA, entra con el formulario y vuelve a donde iba."""
    page.goto(f"{url}{destino}")
    expect(page).to_have_url(re.compile(r"/login\?siguiente="))
    page.get_by_label("Identificador").fill(agente_id)
    page.get_by_label(re.compile("Contrase")).fill(CONTRASENA)
    page.get_by_role("button", name="Entrar").click()
    expect(page).to_have_url(re.compile(re.escape(destino) + "$"))
