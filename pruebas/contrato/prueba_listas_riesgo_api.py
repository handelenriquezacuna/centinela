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

from app.api.dependencias import (
    dep_repo_detalle,
    dep_repo_red_mulas_portal,
    dep_repo_tablero,
)
from app.api.rutas.listas_riesgo import dep_repo_listas_riesgo
from app.main import crear_aplicacion
from app.modelos.alertas import RolAgente
from pruebas.dobles.repo_detalle_falso import RepoDetalleFalso
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
    creada = crear_aplicacion("htmx")
    creada.dependency_overrides[dep_repo_listas_riesgo] = lambda: listas
    creada.dependency_overrides[dep_repo_red_mulas_portal] = lambda: RepoRedMulasFalso(
        red_de_cuatro_saltos(), listas
    )
    creada.dependency_overrides[dep_repo_tablero] = lambda: tablero
    creada.dependency_overrides[dep_repo_detalle] = lambda: RepoDetalleFalso()
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
# Sankey y marcar como mula (T-55)
# ---------------------------------------------------------------------------

MARCAR = "/api/v1/listas-riesgo"
MARCAR_PORTAL = "/red-mulas/8712-4455/mulas/6033-9002?desde=2026-11-19T03:47:00%2B00:00"


@pytest.mark.asyncio
async def prueba_la_pagina_de_la_red_dibuja_el_sankey_con_bandas_proporcionales(
    http: AsyncClient,
) -> None:
    html = (await http.get(PAGINA_RED, headers=_como(RolAgente.AGENTE))).text

    assert 'id="sankey-svg"' in html and 'role="img"' in html
    assert html.count('class="sankey-banda"') == 4
    assert 'data-saltos="4"' in html
    assert re.search(r"<title>₡750\.000 de 8712-4455 a 6033-9001, 18/11/2026 21:47", html)
    grosores = [int(g) for g in re.findall(r'data-grosor="(\d+)"', html)]
    assert grosores == sorted(grosores, reverse=True) and grosores[0] == 140


@pytest.mark.asyncio
async def prueba_el_agente_ve_el_sankey_pero_no_el_boton_de_marcar(http: AsyncClient) -> None:
    html = (await http.get(PAGINA_RED, headers=_como(RolAgente.AGENTE))).text

    assert 'id="sankey-cuentas"' in html and "6033-9002" in html
    assert "Marcar como mula" not in html


@pytest.mark.asyncio
async def prueba_el_supervisor_ve_el_boton_solo_en_las_cuentas_sin_marca(
    http: AsyncClient,
) -> None:
    html = (await http.get(PAGINA_RED, headers=_como(RolAgente.SUPERVISOR))).text

    assert html.count("Marcar como mula") == 4  # 5 cuentas, una ya en lista (6033-9001)
    assert 'hx-post="/red-mulas/8712-4455/mulas/6033-9002?' in html
    assert 'hx-post="/red-mulas/8712-4455/mulas/6033-9001?' not in html
    assert "hx-confirm=\"Se marca la cuenta 6033-9002 como mula" in html
    assert 'data-cuenta="6033-9001" data-columna="1" data-en-lista="si"' in html


@pytest.mark.asyncio
async def prueba_el_fragmento_del_sankey_es_el_mismo_dibujo(http: AsyncClient) -> None:
    respuesta = await http.get(
        "/red-mulas/8712-4455/sankey?desde=2026-11-19T03:47:00", headers=_como(RolAgente.AGENTE)
    )

    assert respuesta.status_code == 200
    assert respuesta.text.lstrip().startswith('<div id="sankey"')
    assert "<html" not in respuesta.text


@pytest.mark.asyncio
async def prueba_con_transaccion_de_partida_la_victima_es_la_columna_0_y_no_tiene_boton(
    http: AsyncClient,
) -> None:
    ruta = "/red-mulas/8712-4455/sankey?desde=2026-11-19T03:47:00&transaccion=TXN-001"
    html = (await http.get(ruta, headers=_como(RolAgente.SUPERVISOR))).text

    assert 'data-saltos="4" data-transferencias="5"' in html
    assert "desde la víctima Maria Rodriguez Vargas: 5 transferencias" in html
    assert html.count('class="sankey-banda"') == 5
    assert re.search(r'data-cuenta="CTA-001" data-columna="0" data-en-lista="no" data-victima="si"', html)
    assert "Víctima: Maria Rodriguez Vargas" in html
    assert 'hx-post="/red-mulas/8712-4455/mulas/CTA-001' not in html
    # Los formularios de marcar repiten la transaccion para volver a dibujar con la victima.
    assert "transaccion=TXN-001" in html and html.count("Marcar como mula") == 4
    grosores = [int(g) for g in re.findall(r'data-grosor="(\d+)"', html)]
    assert grosores[0] == 140  # 750.000 es la mayor banda


@pytest.mark.asyncio
async def prueba_sin_transaccion_de_partida_el_sankey_no_tiene_victima(http: AsyncClient) -> None:
    html = (await http.get(PAGINA_RED, headers=_como(RolAgente.AGENTE))).text

    assert 'data-victima="si"' not in html and "desde la cuenta 8712-4455" in html
    assert 'data-saltos="4" data-transferencias="4"' in html


@pytest.mark.asyncio
async def prueba_marcar_con_transaccion_vuelve_a_dibujar_con_la_victima(
    http: AsyncClient,
) -> None:
    respuesta = await http.post(
        MARCAR_PORTAL + "&transaccion=TXN-001",
        content="motivo=Recibio+el+dinero",
        headers={**_como(RolAgente.SUPERVISOR),
                 "content-type": "application/x-www-form-urlencoded"},
    )

    assert respuesta.status_code == 200
    assert 'data-victima="si"' in respuesta.text and "5 transferencias" in respuesta.text
    assert 'data-cuenta="6033-9002" data-columna="3" data-en-lista="si"' in respuesta.text


@pytest.mark.asyncio
async def prueba_marcar_desde_el_portal_cambia_el_nodo_y_guarda_el_motivo(
    http: AsyncClient, listas: RepoListasRiesgoFalso
) -> None:
    respuesta = await http.post(
        MARCAR_PORTAL,
        content="motivo=Recibio+el+dinero+de+la+victima",
        headers={**_como(RolAgente.SUPERVISOR, "AGT-003"),
                 "content-type": "application/x-www-form-urlencoded"},
    )

    assert respuesta.status_code == 200
    assert 'data-cuenta="6033-9002" data-columna="2" data-en-lista="si"' in respuesta.text
    assert "quedó en lista de riesgo" in respuesta.text
    nueva = listas.entradas[-1]
    assert nueva["cuenta"] == "6033-9002" and nueva["aprobo"] == "AGT-003"
    assert nueva["motivo"] == "Recibio el dinero de la victima"


@pytest.mark.asyncio
@pytest.mark.parametrize("cuerpo", ["motivo=", "motivo=+++", "", "motivo=abc", "motivo=" + "x" * 201])
async def prueba_marcar_desde_el_portal_sin_motivo_valido_es_422_y_no_escribe(
    http: AsyncClient, listas: RepoListasRiesgoFalso, cuerpo: str
) -> None:
    respuesta = await http.post(
        MARCAR_PORTAL,
        content=cuerpo,
        headers={**_como(RolAgente.SUPERVISOR),
                 "content-type": "application/x-www-form-urlencoded"},
    )

    assert respuesta.status_code == 422
    assert "El motivo es obligatorio" in respuesta.text and 'role="alert"' in respuesta.text
    assert [e["cuenta"] for e in listas.entradas] == ["6033-9001"]


@pytest.mark.asyncio
async def prueba_el_agente_no_puede_marcar_ni_por_la_api_ni_por_el_portal(
    http: AsyncClient, listas: RepoListasRiesgoFalso
) -> None:
    api = await http.post(
        MARCAR,
        json={"cuenta": "6033-9002", "motivo": "Recibio el dinero"},
        headers=_como(RolAgente.AGENTE),
    )
    portal = await http.post(
        MARCAR_PORTAL, content="motivo=Recibio+el+dinero", headers=_como(RolAgente.AGENTE)
    )

    assert api.status_code == portal.status_code == 403
    assert [e["cuenta"] for e in listas.entradas] == ["6033-9001"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        {"cuenta": "6033-9002"},
        {"cuenta": "6033-9002", "motivo": ""},
        {"cuenta": "6033-9002", "motivo": "    "},
        {"cuenta": "6033-9002", "motivo": "x" * 201},
        {"cuenta": "no-es-sinpe", "motivo": "Recibio el dinero"},
        {"cuenta": "6033-9002", "motivo": "Recibio el dinero", "vigente": False},
    ],
)
async def prueba_marcar_por_la_api_con_cuerpo_invalido_es_422(
    http: AsyncClient, listas: RepoListasRiesgoFalso, cuerpo: dict[str, Any]
) -> None:
    respuesta = await http.post(MARCAR, json=cuerpo, headers=_como(RolAgente.SUPERVISOR))

    assert respuesta.status_code == 422
    assert set(respuesta.json()["error"]) == {"codigo", "mensaje", "detalles"}
    assert len(listas.entradas) == 1


@pytest.mark.asyncio
async def prueba_marcar_por_la_api_crea_201_y_es_idempotente(
    http: AsyncClient, listas: RepoListasRiesgoFalso
) -> None:
    cuerpo = {"cuenta": "6033-9002", "motivo": "  Recibio el dinero  "}
    primera = await http.post(MARCAR, json=cuerpo, headers=_como(RolAgente.SUPERVISOR, "AGT-003"))
    segunda = await http.post(MARCAR, json=cuerpo, headers=_como(RolAgente.ADMINISTRADOR))

    assert (primera.status_code, segunda.status_code) == (201, 200)
    assert primera.json()["entrada"]["motivo"] == "Recibio el dinero"
    assert primera.json()["creada"] is True and segunda.json()["creada"] is False
    assert listas.entradas[-1]["aprobo"] == "AGT-003"
    assert len([e for e in listas.entradas if e["cuenta"] == "6033-9002"]) == 1


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
