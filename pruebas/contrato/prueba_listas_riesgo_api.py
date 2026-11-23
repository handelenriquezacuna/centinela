"""Aprobar propuestas de la red y dar de baja (T-51, D-41): contrato HTTP con dobles. Sin Mongo.

El detalle contra Mongo de verdad (la entrada vigente, el borrado real, R-07 en la
siguiente transaccion) esta en `pruebas/integracion/prueba_listas_riesgo_mongo.py`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_repo_red_mulas_portal, dep_repo_tablero
from app.api.rutas.listas_riesgo import dep_repo_listas_riesgo
from app.main import crear_aplicacion
from app.modelos.alertas import RolAgente
from pruebas.dobles.repo_tablero_falso import (
    RepoListasRiesgoFalso,
    RepoRedMulasFalso,
    RepoTableroFalso,
    indicador,
    red_de_cuatro_saltos,
)
from pruebas.sesiones import encabezados_de

APROBAR = "/api/v1/red-mulas/8712-4455/propuestas/6033-9002"
PAGINA_RED = "/red-mulas/8712-4455?desde=2026-11-19T03:47:00"


def _entrada(riesgo_id: str = "RSK-001", cuenta: str = "6033-9001") -> dict[str, Any]:
    return {
        "_id": riesgo_id,
        "cuenta": cuenta,
        "motivo": "Primer salto de una cadena de fragmentacion",
        "fecha_reporte": datetime(2026, 9, 25, 15, tzinfo=UTC),
        "vigente": True,
        "esquema_version": 1,
    }


@pytest_asyncio.fixture
async def listas() -> RepoListasRiesgoFalso:
    return RepoListasRiesgoFalso([_entrada()])


@pytest.fixture
def tablero() -> RepoTableroFalso:
    return RepoTableroFalso()


@pytest_asyncio.fixture
async def http(listas: RepoListasRiesgoFalso, tablero: RepoTableroFalso) -> AsyncIterator[AsyncClient]:
    creada = crear_aplicacion()
    creada.dependency_overrides[dep_repo_listas_riesgo] = lambda: listas
    creada.dependency_overrides[dep_repo_red_mulas_portal] = lambda: RepoRedMulasFalso(
        red_de_cuatro_saltos()
    )
    creada.dependency_overrides[dep_repo_tablero] = lambda: tablero
    async with LifespanManager(creada) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as cliente:
        yield cliente


def _como(rol: RolAgente, agente: str = "AGT-002") -> dict[str, str]:
    return encabezados_de(rol, agente)


# ---------------------------------------------------------------------------
# Permisos: el agente recibe 403 y no se escribe nada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_agente_recibe_403_al_aprobar_y_al_borrar(
    http: AsyncClient, listas: RepoListasRiesgoFalso
) -> None:
    aprobar = await http.post(APROBAR, headers=_como(RolAgente.AGENTE))
    borrar = await http.delete("/api/v1/listas-riesgo/RSK-001", headers=_como(RolAgente.AGENTE))

    for respuesta in (aprobar, borrar):
        assert respuesta.status_code == 403
        assert respuesta.json()["error"]["codigo"] == "prohibido"
    assert [e["_id"] for e in listas.entradas] == ["RSK-001"]
    assert listas.bajas == []


@pytest.mark.asyncio
async def prueba_el_agente_recibe_403_tambien_en_las_rutas_del_portal(http: AsyncClient) -> None:
    alta = await http.post(
        "/red-mulas/8712-4455/propuestas/6033-9002", headers=_como(RolAgente.AGENTE)
    )
    baja = await http.post(
        "/red-mulas/8712-4455/listas-riesgo/RSK-001/baja", headers=_como(RolAgente.AGENTE)
    )

    assert alta.status_code == baja.status_code == 403


# ---------------------------------------------------------------------------
# Aprobar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("rol", [RolAgente.SUPERVISOR, RolAgente.ADMINISTRADOR])
async def prueba_aprobar_crea_la_entrada_con_el_motivo_y_la_fecha_de_ahora(
    http: AsyncClient, listas: RepoListasRiesgoFalso, rol: RolAgente
) -> None:
    antes = datetime.now(UTC)
    respuesta = await http.post(APROBAR, headers=_como(rol, "AGT-003"))

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["creada"] is True
    entrada = cuerpo["entrada"]
    assert entrada["cuenta"] == "6033-9002" and entrada["vigente"] is True
    assert entrada["motivo"] == "red de mulas desde 8712-4455"
    assert datetime.fromisoformat(entrada["fecha_reporte"]) >= antes
    assert listas.entradas[-1]["aprobo"] == "AGT-003"  # quien aprobo sale de la sesion


@pytest.mark.asyncio
async def prueba_aprobar_dos_veces_no_duplica(
    http: AsyncClient, listas: RepoListasRiesgoFalso
) -> None:
    primera = await http.post(APROBAR, headers=_como(RolAgente.SUPERVISOR))
    segunda = await http.post(APROBAR, headers=_como(RolAgente.SUPERVISOR))

    assert (primera.status_code, segunda.status_code) == (201, 200)
    assert segunda.json()["creada"] is False
    assert segunda.json()["entrada"]["_id"] == primera.json()["entrada"]["_id"]
    assert len([e for e in listas.entradas if e["cuenta"] == "6033-9002"]) == 1


@pytest.mark.asyncio
async def prueba_aprobar_con_una_cuenta_mal_formada_es_422_en_la_forma_unica(
    http: AsyncClient,
) -> None:
    respuesta = await http.post(
        "/api/v1/red-mulas/8712-4455/propuestas/no-es-sinpe", headers=_como(RolAgente.SUPERVISOR)
    )

    assert respuesta.status_code == 422
    assert set(respuesta.json()["error"]) == {"codigo", "mensaje", "detalles"}


# ---------------------------------------------------------------------------
# Dar de baja
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_dar_de_baja_borra_y_deja_rastro_en_el_log(
    http: AsyncClient, listas: RepoListasRiesgoFalso
) -> None:
    respuesta = await http.delete(
        "/api/v1/listas-riesgo/RSK-001", headers=_como(RolAgente.SUPERVISOR, "AGT-002")
    )

    assert respuesta.status_code == 204 and respuesta.content == b""
    assert listas.entradas == []
    assert listas.bajas == [("RSK-001", "AGT-002")]


@pytest.mark.asyncio
async def prueba_dar_de_baja_algo_que_no_existe_es_404_en_la_forma_unica(
    http: AsyncClient,
) -> None:
    respuesta = await http.delete(
        "/api/v1/listas-riesgo/RSK-777", headers=_como(RolAgente.ADMINISTRADOR)
    )

    assert respuesta.status_code == 404
    error = respuesta.json()["error"]
    assert error["codigo"] == "no_encontrado" and "RSK-777" in error["mensaje"]
    assert set(error) == {"codigo", "mensaje", "detalles"}


@pytest.mark.asyncio
async def prueba_un_identificador_mal_formado_es_422(http: AsyncClient) -> None:
    respuesta = await http.delete("/api/v1/listas-riesgo/xx", headers=_como(RolAgente.SUPERVISOR))

    assert respuesta.status_code == 422


# ---------------------------------------------------------------------------
# Pantallas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_supervisor_ve_un_boton_por_propuesta_y_las_bajas(http: AsyncClient) -> None:
    html = (await http.get(PAGINA_RED, headers=_como(RolAgente.SUPERVISOR))).text

    assert html.count("Agregar a lista de riesgo") == 3  # las tres propuestas
    assert 'hx-post="/red-mulas/8712-4455/propuestas/6033-9002"' in html
    assert "Dar de baja" in html and "RSK-001" in html


@pytest.mark.asyncio
async def prueba_el_agente_ve_las_propuestas_sin_botones(http: AsyncClient) -> None:
    html = (await http.get(PAGINA_RED, headers=_como(RolAgente.AGENTE))).text

    assert "6033-9002" in html
    assert "Agregar a lista de riesgo" not in html and "Dar de baja" not in html


@pytest.mark.asyncio
async def prueba_el_formulario_htmx_aprueba_y_devuelve_la_fila(
    http: AsyncClient, listas: RepoListasRiesgoFalso
) -> None:
    ruta = "/red-mulas/8712-4455/propuestas/6033-9002"
    primera = await http.post(ruta, headers=_como(RolAgente.SUPERVISOR))
    segunda = await http.post(ruta, headers=_como(RolAgente.SUPERVISOR))

    assert primera.status_code == segunda.status_code == 200
    assert "Agregada como" in primera.text and "no se duplico" in segunda.text
    assert len([e for e in listas.entradas if e["cuenta"] == "6033-9002"]) == 1


@pytest.mark.asyncio
async def prueba_el_formulario_htmx_da_de_baja_y_un_inexistente_es_404(
    http: AsyncClient, listas: RepoListasRiesgoFalso
) -> None:
    ok = await http.post(
        "/red-mulas/8712-4455/listas-riesgo/RSK-001/baja", headers=_como(RolAgente.SUPERVISOR)
    )
    falta = await http.post(
        "/red-mulas/8712-4455/listas-riesgo/RSK-001/baja", headers=_como(RolAgente.SUPERVISOR)
    )

    assert ok.status_code == 200 and "Dada de baja" in ok.text and listas.entradas == []
    assert falta.status_code == 404 and "No existe la entrada RSK-001" in falta.text


# ---------------------------------------------------------------------------
# Tablero: las cifras de arriba
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_tablero_muestra_los_kpi_con_comparacion(
    http: AsyncClient, tablero: RepoTableroFalso
) -> None:
    marca = datetime(2026, 11, 20, 3, 30, tzinfo=UTC)
    docs = [
        indicador("2026-11-18", 3, (0, 0, 3), (0, 0, 2, 1, 0), calculado_en=marca,
                  decision_cantidad=3, decision_segundos_total=900,
                  monto_protegido_crc=200_000, pendientes=0),
        indicador("2026-11-19", 5, (0, 0, 5), (1, 1, 2, 1, 0), calculado_en=marca,
                  decision_cantidad=4, decision_segundos_total=2400,
                  monto_protegido_crc=500_000, pendientes=2,
                  pendiente_mas_antigua=datetime(2026, 11, 19, 14, tzinfo=UTC)),
    ]
    tablero.documentos = docs
    html = (await http.get("/tablero?dias=1", headers=_como(RolAgente.SUPERVISOR))).text

    assert 'id="kpis"' in html
    assert "10 min" in html  # 600 s: 2400 / 4
    assert "5 min" in html  # el dia previo: 900 / 3 = 300 s
    assert re.search(r"500\D000", html) and re.search(r"200\D000", html)  # sea cual sea el formato
    assert "66.7%" in html and "contra el periodo anterior" in html  # 2 / (2 + 1)
    assert "la más antigua sin atender" in html
