"""Tablero (H-20) y grafo de la red de mulas (T-35) con dobles. Sin Mongo."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import (
    dep_repo_alertas,
    dep_repo_detalle,
    dep_repo_red_mulas_portal,
    dep_repo_reglas,
    dep_repo_tablero,
)
from app.api.rutas.paginas_tablero import barras_horizontales, columnas_por_hora
from app.main import crear_aplicacion
from app.repos.tablero import resumir
from pruebas.dobles.catalogo_reglas import (
    politica_como_documento,
    reglas_como_documentos,
)
from pruebas.dobles.repo_detalle_falso import RepoDetalleFalso, alerta_v2
from pruebas.dobles.repo_tablero_falso import (
    RepoRedMulasFalso,
    RepoTableroFalso,
    documentos_con_comparacion,
    red_de_cuatro_saltos,
)
from pruebas.dobles.repos_falsos import RepoAlertasFalso, RepoReglasFalso
from pruebas.sesiones import encabezados_de

Armar = Callable[..., Awaitable[AsyncClient]]


@pytest_asyncio.fixture
async def armar() -> AsyncIterator[Armar]:
    abiertos: list[Any] = []

    async def _armar(
        tablero: RepoTableroFalso | None = None, red: RepoRedMulasFalso | None = None
    ) -> AsyncClient:
        creada = crear_aplicacion()
        alertas = RepoAlertasFalso([alerta_v2()])
        creada.dependency_overrides[dep_repo_alertas] = lambda: alertas
        creada.dependency_overrides[dep_repo_detalle] = lambda: RepoDetalleFalso(alertas.casos)
        creada.dependency_overrides[dep_repo_tablero] = lambda: tablero or RepoTableroFalso()
        creada.dependency_overrides[dep_repo_red_mulas_portal] = lambda: red or RepoRedMulasFalso()
        # El detalle lee la politica vigente: sin el doble, la prueba iria a Mongo.
        reglas = RepoReglasFalso(reglas_como_documentos(), politica_como_documento())
        creada.dependency_overrides[dep_repo_reglas] = lambda: reglas
        gestor = LifespanManager(creada)
        gestionada = await gestor.__aenter__()
        http = AsyncClient(headers=encabezados_de(), transport=ASGITransport(app=gestionada.app), base_url="http://pruebas")
        abiertos.append((gestor, http))
        return http

    yield _armar
    for gestor, http in abiertos:
        await http.aclose()
        await gestor.__aexit__(None, None, None)


@pytest.mark.asyncio
async def prueba_tablero_vacio_dice_que_falta_y_sugiere_el_comando(armar: Armar) -> None:
    http = await armar()
    respuesta = await http.get("/tablero")
    assert respuesta.status_code == 200
    assert "indicadores_diarios" in respuesta.text and "esta vacia" in respuesta.text
    assert "python tareas.py indicadores" in respuesta.text
    assert "En construccion" not in respuesta.text


@pytest.mark.asyncio
async def prueba_tablero_con_datos_compara_contra_el_periodo_anterior(armar: Armar) -> None:
    http = await armar(RepoTableroFalso(documentos_con_comparacion()))
    respuesta = await http.get("/tablero?dias=2")
    assert respuesta.status_code == 200
    html = respuesta.text
    # Actual (18 y 19): 15 alertas. Anterior (16 y 17): 10. Variacion +5 (+50.0%).
    assert re.search(r'id="total-actual">15<', html)
    assert re.search(r'id="total-previo">10<', html)
    assert "+5 (50.0%)" in html
    # Marca del ultimo calculo en hora de Costa Rica (UTC-6): 21:30 del 19.
    assert "2026-11-19 21:30" in html
    # Graficos propios en SVG, uno por dimension, sin CDN.
    assert html.count("<svg") >= 4 and "unpkg" not in html and "cdn." not in html
    assert "AGT-001" in html and "CRITICA" in html and "escalada" in html
    assert "22:00 - 10" in html  # hora 22 (CR) suma 5 + 5
    assert "no trae el desglose" not in html


@pytest.mark.asyncio
async def prueba_tablero_sin_periodo_previo_no_inventa_porcentaje(armar: Armar) -> None:
    http = await armar(RepoTableroFalso(documentos_con_comparacion()[2:]))
    html = (await http.get("/tablero?dias=2")).text
    assert "sin periodo anterior con que comparar" in html


@pytest.mark.asyncio
async def prueba_tablero_avisa_si_el_rollup_no_trae_agente_ni_hora(armar: Armar) -> None:
    http = await armar(RepoTableroFalso(documentos_con_comparacion()[:2]))
    html = (await http.get("/tablero?dias=2")).text
    assert "todavia no trae el desglose por agente" in html
    assert "todavia no trae el desglose por hora" in html


@pytest.mark.asyncio
async def prueba_tablero_acota_el_periodo(armar: Armar) -> None:
    http = await armar()
    assert (await http.get("/tablero?dias=0")).status_code == 422
    assert (await http.get("/tablero?dias=31")).status_code == 422


def prueba_resumir_ancla_el_periodo_en_el_ultimo_rollup() -> None:
    resumen = resumir(documentos_con_comparacion(), 2)
    assert resumen["periodo_actual"] == {"desde": "2026-11-18", "hasta": "2026-11-19"}
    assert resumen["periodo_previo"] == {"desde": "2026-11-16", "hasta": "2026-11-17"}
    assert resumen["severidad"]["CRITICA"]["delta"] == 3  # 5 contra 2
    assert resumen["actual"]["por_agente"] == {"AGT-001": 9, "AGT-002": 6}
    assert resumir([], 7) == {"vacio": True, "dias": 7}


def prueba_las_barras_chicas_no_desaparecen() -> None:
    grafico = barras_horizontales([("a", 1000, "x"), ("b", 1, "x"), ("c", 0, "x")])
    assert [f["ancho"] for f in grafico["filas"]][1:] == [2, 0]
    assert columnas_por_hora({"00": 0, "01": 1})["columnas"][1]["alto"] == 120


@pytest.mark.asyncio
async def prueba_red_de_cuatro_saltos_dibuja_nodos_aristas_y_propuestas(armar: Armar) -> None:
    doble = RepoRedMulasFalso(red_de_cuatro_saltos())
    http = await armar(red=doble)
    respuesta = await http.get("/red-mulas/8712-4455?desde=2026-11-19T03:47:00")
    assert respuesta.status_code == 200
    html = respuesta.text
    assert html.count('class="nodo-grupo"') == 5
    assert html.count('class="arista"') == 4
    for monto in ("750 000 CRC", "740 000 CRC", "720 000 CRC", "700 000 CRC"):
        assert monto in html
    for minutos in ("12.5 min", "8.0 min", "21.0 min"):
        assert minutos in html
    assert "externa" in html and "en lista de riesgo" in html
    assert re.search(r'id="propuestas">.*7000-0001', html, flags=re.DOTALL)
    assert "el sistema no escribe en la lista" in html
    assert doble.pedidos[0]["cuenta"] == "8712-4455" and doble.pedidos[0]["desde"] is not None
    assert "unpkg" not in html


@pytest.mark.asyncio
async def prueba_red_de_cuenta_inexistente_es_404_con_mensaje(armar: Armar) -> None:
    http = await armar(red=RepoRedMulasFalso(None))
    respuesta = await http.get("/red-mulas/CTA-999")
    assert respuesta.status_code == 404
    assert "no existe" in respuesta.text


@pytest.mark.asyncio
async def prueba_red_sin_salidas_lo_dice_y_cuenta_invalida_es_422(armar: Armar) -> None:
    red = red_de_cuatro_saltos().model_copy(
        update={"aristas": [], "nodos": [], "propuestas_lista_riesgo": []}
    )
    http = await armar(red=RepoRedMulasFalso(red))
    assert "no hay red que dibujar" in (await http.get("/red-mulas/6033-9001")).text
    assert (await http.get("/red-mulas/cualquiera")).status_code == 422


@pytest.mark.asyncio
async def prueba_el_formulario_redirige_y_la_navegacion_y_el_detalle_enlazan(armar: Armar) -> None:
    http = await armar()
    redireccion = await http.get("/red-mulas?cuenta=6033-9001", follow_redirects=False)
    assert redireccion.status_code in (302, 307)
    assert redireccion.headers["location"] == "/red-mulas/6033-9001"
    formulario = await http.get("/red-mulas")
    assert formulario.status_code == 200 and 'href="/red-mulas"' in formulario.text
    detalle = (await http.get("/detalle/ALR-001")).text
    assert 'href="/red-mulas/6033-9001?desde=2026-10-15T04%3A47%3A00%2B00%3A00"' in detalle
