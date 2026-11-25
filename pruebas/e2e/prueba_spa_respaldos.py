"""D-74 en la SPA: el administrador crea y verifica un respaldo; los demas roles no lo ven.

Contra `servidor_e2e` (la SPA): navegador -> API -> `mongodump` y `mongorestore` reales ->
base `antifraude_pruebas_e2e` y su base temporal. El respaldo cae en un directorio temporal
(el conftest lo fija). Lo que se mira al final no es la pantalla sino el servidor: que la base
temporal de la verificacion ya no existe.
"""

from __future__ import annotations

import re
import shutil
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from pruebas.conftest import saltar_o_fallar
from pruebas.e2e.conftest import (
    ADMINISTRADOR,
    AGENTE,
    BASE_E2E,
    SUPERVISOR,
    con_base,
    iniciar_sesion,
)

pytestmark = pytest.mark.e2e

BASE_TEMPORAL = f"{BASE_E2E}_verificacion"


@pytest.fixture(autouse=True)
def herramientas() -> None:
    if shutil.which("mongodump") is None or shutil.which("mongorestore") is None:
        saltar_o_fallar("mongodump y mongorestore (mongodb-database-tools) no estan en el PATH")


async def _bases(base: Any) -> list[str]:
    return await base.client.list_database_names()


def prueba_el_administrador_crea_y_verifica_un_respaldo(page: Page) -> None:
    iniciar_sesion(page, ADMINISTRADOR)
    expect(page).to_have_url(re.compile(r"/administracion/salud$"))

    page.get_by_role("link", name="Respaldos").click()
    expect(page).to_have_url(re.compile(r"/administracion/respaldos$"))
    expect(page.get_by_role("heading", name="Respaldos", exact=True)).to_be_visible()

    page.get_by_role("button", name="Crear respaldo").click()
    page.get_by_role("dialog").get_by_role("button", name="Crear respaldo").click()
    fila = page.locator("table.tabla-responsiva tbody tr").first
    expect(fila.locator("code").first).to_contain_text("RSP-", timeout=60_000)
    expect(fila).to_contain_text(ADMINISTRADOR)
    expect(fila).to_contain_text("Sin verificar")
    # Restaurar no es un boton: la pantalla muestra el comando.
    expect(fila).to_contain_text("python tareas.py restaurar --archivo RSP-")
    expect(page.get_by_role("button", name=re.compile("restaurar", re.IGNORECASE))).to_have_count(0)

    page.get_by_role("button", name=re.compile(r"^Verificar RSP-")).click()
    expect(fila.locator(".insignia")).to_have_text("Correcta", timeout=60_000)
    expect(page.get_by_role("status").filter(has_text="Verificación correcta")).to_be_visible()

    # Lo que importa esta en el servidor: la base temporal no quedo.
    assert BASE_TEMPORAL not in con_base(_bases)

    # La lista sobrevive a una recarga: el manifiesto esta en disco, no en la pantalla.
    page.reload()
    expect(page.locator("table.tabla-responsiva tbody tr").first.locator(".insignia")).to_have_text("Correcta")


@pytest.mark.parametrize("agente_id", [AGENTE, SUPERVISOR])
def prueba_agente_y_supervisor_no_ven_respaldos_y_la_ruta_los_niega(
    page: Page, agente_id: str
) -> None:
    iniciar_sesion(page, agente_id)
    expect(page).not_to_have_url(re.compile(r"/login"))

    expect(page.get_by_role("link", name="Respaldos")).to_have_count(0)

    page.goto("/administracion/respaldos")
    expect(page.get_by_role("heading", name="Acceso denegado")).to_be_visible()
    respuesta = page.request.get("/api/v1/respaldos")
    assert respuesta.status == 403
