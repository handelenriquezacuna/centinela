"""La SPA (T-66, fase 2C): tablero del supervisor y reglas con su politica.

Contra `servidor_e2e` (la SPA, interfaz por omision desde T-67), igual que `prueba_spa_panel.py`: navegador
-> index.html de la SPA -> API JSON -> Mongo. Los selectores son por rol y etiqueta
accesible. Lo que estas pruebas editan (reglas, politica, rollup) vive en la base e2e y se
devuelve a como estaba al terminar: nunca se toca la base viva.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import Int64
from playwright.sync_api import Page, expect

from app.repos.indicadores import documento_en_ceros, hoy_costa_rica
from pruebas.e2e.conftest import AGENTE, CONTRASENA, SUPERVISOR, con_base

pytestmark = pytest.mark.e2e

COLECCIONES_EDITABLES = ("reglas_deteccion", "politica_deteccion")


def _entrar(page: Page, url: str, agente_id: str) -> None:
    page.goto(f"{url}/login")
    page.get_by_label("Identificador").fill(agente_id)
    page.get_by_label(re.compile("Contrase")).fill(CONTRASENA)
    page.get_by_role("button", name="Entrar").click()
    # Cada rol aterriza en su inicio: el supervisor en el tablero, el agente en su cola.
    expect(page).to_have_url(re.compile(r"/tablero$" if agente_id == SUPERVISOR else r"/panel$"))


async def _foto(base: Any) -> dict[str, list[dict[str, Any]]]:
    return {c: await base[c].find().to_list(length=None) for c in COLECCIONES_EDITABLES}


async def _volver(base: Any, foto: dict[str, list[dict[str, Any]]]) -> None:
    for coleccion, documentos in foto.items():
        await base[coleccion].delete_many({"_id": {"$nin": [d["_id"] for d in documentos]}})
        for documento in documentos:
            await base[coleccion].replace_one({"_id": documento["_id"]}, documento, upsert=True)


@pytest.fixture
def catalogo_intacto() -> Iterator[None]:
    """Lo que una prueba edite de reglas y politica vuelve a como estaba."""
    foto = con_base(_foto)
    try:
        yield
    finally:
        con_base(lambda base: _volver(base, foto))


async def _leer(base: Any, coleccion: str, documento_id: str) -> dict[str, Any]:
    return await base[coleccion].find_one({"_id": documento_id})


async def _sembrar_rollup(base: Any) -> str:
    """Un dia de rollup con cifras fijas, un dia despues de hoy.

    D3 recalcula el dia de HOY cada pocos segundos, asi que un dia posterior es el unico que
    nadie pisa y el que el tablero toma como el ultimo con datos (ancla el periodo en el
    ultimo rollup). Devuelve la fecha para borrarlo.
    """
    fecha = (hoy_costa_rica() + timedelta(days=1)).isoformat()
    documento = documento_en_ceros(fecha, datetime.now(UTC))
    documento.update(
        total_alertas=5,
        por_severidad={"CRITICA": 2, "ALTA": 2, "MEDIA": 1},
        por_estado={"nueva": 2, "en_revision": 1, "confirmada": 1, "descartada": 0, "escalada": 1},
        por_agente={"AGT-901": 3, "AGT-902": 1},
        decision_cantidad=2,
        decision_segundos_total=5400,
        monto_protegido_crc=Int64(1_250_000),
        pendientes=3,
        pendiente_mas_antigua=datetime.now(UTC) - timedelta(hours=5),
    )
    documento["por_hora"]["22"] = 4
    documento["por_hora"]["09"] = 1
    await base["indicadores_diarios"].replace_one({"_id": fecha}, documento, upsert=True)
    return fecha


async def _borrar_rollup(base: Any, fecha: str) -> None:
    await base["indicadores_diarios"].delete_one({"_id": fecha})


def prueba_el_agente_no_entra_al_tablero_ni_a_reglas_en_la_spa(page: Page, servidor_e2e: str) -> None:
    _entrar(page, servidor_e2e, AGENTE)

    expect(page.get_by_role("navigation", name="Navegación principal").get_by_role("link", name="Tablero")).to_have_count(0)
    for ruta in ("/tablero", "/reglas"):
        respuesta = page.goto(f"{servidor_e2e}{ruta}")
        assert respuesta is not None and respuesta.status == 403
        expect(page.get_by_role("heading", name="Acceso denegado")).to_be_visible()
    expect(page.get_by_role("heading", name="Tablero de indicadores")).to_have_count(0)


def prueba_el_supervisor_ve_el_tablero_con_kpis_graficos_y_marca_de_calculo(
    page: Page, servidor_e2e: str
) -> None:
    fecha = con_base(_sembrar_rollup)
    try:
        _entrar(page, servidor_e2e, SUPERVISOR)
        page.get_by_role("navigation", name="Navegación principal").get_by_role("link", name="Tablero").click()

        expect(page).to_have_url(re.compile(r"/tablero$"))
        expect(page.get_by_role("heading", name="Tablero de indicadores")).to_be_visible()
        # El periodo por omision (7 dias) sumaria el dia de hoy, que D3 recalcula cada pocos
        # segundos y otras e2e pueden mover. El periodo de 1 dia se ancla en el ultimo rollup
        # (el sembrado, un dia despues de hoy), asi que solo mira la fecha sembrada.
        with page.expect_response(re.compile(r"/api/v1/tablero\?dias=1\b")):
            page.get_by_role("navigation", name="Periodo").get_by_role("link", name="1 día").click()
        expect(page).to_have_url(re.compile(r"/tablero\?dias=1$"))
        expect(page.locator("#marca-calculo")).to_contain_text(f"Periodo: {fecha} a {fecha} (1 con datos)")
        expect(page.locator("#marca-calculo")).to_contain_text("Último cálculo:")
        expect(page.locator("#marca-calculo")).to_contain_text("(hora CR)")
        # Los numeros son los del rollup: la SPA no recalcula.
        expect(page.locator("#total-actual")).to_have_text("5")
        expect(page.locator("#kpi-monto .metrica-valor")).to_have_text("₡1.250.000")
        expect(page.locator("#kpi-tiempo .metrica-valor")).to_have_text("45 min 0 s")
        expect(page.locator("#kpi-pendientes .metrica-valor")).to_have_text("3")
        expect(page.locator("#kpi-tiempo")).to_contain_text("Tiempo hasta la decisión")
        expect(page.locator("#kpi-tasa")).to_contain_text("Tasa de confirmación")
        expect(page.locator("#kpi-pendientes")).to_contain_text("Pendientes")
        for nombre in ("Alertas por estado", "Alertas por severidad"):
            expect(page.get_by_role("img", name=re.compile(f"^{nombre}:"))).to_be_visible()
        expect(page.get_by_role("img", name=re.compile("^Alertas por hora: 09:00 1, 22:00 4$"))).to_be_visible()
        expect(page.get_by_role("img", name="Alertas por agente: AGT-901 3, AGT-902 1")).to_be_visible()
        expect(page.get_by_role("table", name=re.compile("severidad y estado"))).to_be_visible()
        expect(page.get_by_role("heading", name="Efectividad del motor")).to_be_visible()

        # El periodo vive en la URL y se pide de nuevo al servidor.
        with page.expect_response(re.compile(r"/api/v1/tablero\?dias=14")):
            page.get_by_role("navigation", name="Periodo").get_by_role("link", name="14 días").click()
        expect(page).to_have_url(re.compile(r"/tablero\?dias=14$"))
    finally:
        con_base(lambda base: _borrar_rollup(base, fecha))


def prueba_editar_el_umbral_de_una_regla_sube_la_version_y_conserva_la_anterior(
    page: Page, servidor_e2e: str, catalogo_intacto: None
) -> None:
    _entrar(page, servidor_e2e, SUPERVISOR)
    page.goto(f"{servidor_e2e}/reglas")

    tarjeta = page.locator("#regla-REG-001")
    expect(tarjeta).to_contain_text("versión 1")
    tarjeta.get_by_label("Umbral").fill("20")
    tarjeta.get_by_role("button", name="Guardar").click()

    expect(tarjeta).to_contain_text("Regla R-01 guardada: ahora está en la versión 2.")
    expect(tarjeta.locator(".chip", has_text="versión 2")).to_be_visible()
    tarjeta.get_by_text("Versiones anteriores (1)").click()
    expect(tarjeta.get_by_role("table", name="Versiones anteriores de R-01")).to_be_visible()

    guardada = con_base(lambda base: _leer(base, "reglas_deteccion", "REG-001"))
    assert guardada["version"] == 2 and guardada["umbral"] == 20
    assert [v["version"] for v in guardada["versiones_anteriores"]] == [1]


def prueba_dos_supervisores_con_la_misma_pantalla_el_segundo_recibe_un_aviso_claro(
    page: Page, servidor_e2e: str, catalogo_intacto: None
) -> None:
    _entrar(page, servidor_e2e, SUPERVISOR)
    page.goto(f"{servidor_e2e}/reglas")
    tarjeta = page.locator("#regla-REG-001")
    expect(tarjeta).to_contain_text("versión 1")

    # Otra persona edita en el medio: la pantalla de esta todavia dice version 1.
    respuesta = page.request.patch(
        f"{servidor_e2e}/api/v1/reglas/REG-001", data={"peso": 33, "version_esperada": 1}
    )
    assert respuesta.status == 200

    tarjeta.get_by_label("Umbral").fill("15")
    tarjeta.get_by_role("button", name="Guardar").click()

    aviso = tarjeta.get_by_role("alert")
    expect(aviso).to_contain_text("La regla R-01 cambió mientras se editaba, así que no se guardó nada")
    expect(tarjeta.locator(".chip", has_text="versión 2")).to_be_visible()
    guardada = con_base(lambda base: _leer(base, "reglas_deteccion", "REG-001"))
    assert guardada["version"] == 2 and guardada["umbral"] != 15


def prueba_simular_txn_001_da_75_critica_sin_escribir(
    page: Page, servidor_e2e: str, catalogo_intacto: None
) -> None:
    _entrar(page, servidor_e2e, SUPERVISOR)
    page.goto(f"{servidor_e2e}/reglas")

    simulador = page.get_by_role("form", name="Simular una transacción")
    simulador.get_by_role("button", name="Cargar el ejemplo TXN-001").click()
    simulador.get_by_role("button", name="Simular").click()

    expect(page.get_by_role("heading", name="Resultado para TXN-001")).to_be_visible()
    expect(page.locator("#simulacion-puntaje")).to_have_text("75")
    expect(page.locator("#simulacion-severidad")).to_have_text("CRITICA")

    # Con R-01 en umbral 20 baja a 40 MEDIA, y el catalogo guardado no se movio.
    simulador.get_by_label("Regla propuesta").select_option("REG-001")
    simulador.get_by_label("Umbral propuesto").fill("20")
    simulador.get_by_role("button", name="Simular").click()
    expect(page.locator("#simulacion-puntaje")).to_have_text("40")
    expect(page.locator("#simulacion-severidad")).to_have_text("MEDIA")
    guardada = con_base(lambda base: _leer(base, "reglas_deteccion", "REG-001"))
    assert guardada["version"] == 1


def prueba_editar_la_politica_la_versiona(
    page: Page, servidor_e2e: str, catalogo_intacto: None
) -> None:
    _entrar(page, servidor_e2e, SUPERVISOR)
    page.goto(f"{servidor_e2e}/reglas")

    expect(page.locator("#politica-version")).to_have_text("versión 1")
    formulario = page.get_by_role("form", name="Editar la política")
    formulario.get_by_label("Umbral de alerta").fill("45")
    formulario.get_by_label("Desde (Media)").fill("45")
    formulario.get_by_role("button", name="Guardar política").click()

    expect(page.locator("#politica-version")).to_have_text("versión 2")
    expect(page.locator("#politica-umbral")).to_have_text("45")
    guardada = con_base(lambda base: _leer(base, "politica_deteccion", "POL-001"))
    assert guardada["version"] == 2 and guardada["umbral_alerta"] == 45
    assert guardada["cortes_severidad"][0]["puntaje_minimo"] == 45

    # Un hueco en los cortes lo rechaza el servidor y se dice por tramo.
    formulario.get_by_label("Desde (Alta)").fill("60")
    formulario.get_by_role("button", name="Guardar política").click()
    expect(page.get_by_role("alert").filter(has_text="cortes_severidad")).to_be_visible()
