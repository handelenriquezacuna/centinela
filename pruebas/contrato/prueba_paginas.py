"""Paginas del portal (H-17 panel, T-16 reglas) con repositorios falsos. Sin Mongo."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_repo_alertas, dep_repo_reglas
from app.main import crear_aplicacion
from pruebas.dobles.catalogo_reglas import (
    politica_como_documento,
    reglas_como_documentos,
)
from pruebas.dobles.repos_falsos import RepoAlertasFalso, RepoReglasFalso

# Puntaje coherente con los cortes de POL-001 v2: el panel ordena por puntaje (D-20).
PUNTAJE_POR_SEVERIDAD = {"CRITICA": 75, "ALTA": 60, "MEDIA": 40}


def _alerta(numero: int, severidad: str, minuto: int) -> dict[str, Any]:
    return {
        "_id": f"ALR-{numero:03d}",
        "transaccion_id": f"TXN-{numero:03d}",
        "puntaje": PUNTAJE_POR_SEVERIDAD[severidad],
        "severidad": severidad,
        "estado": "nueva",
        "fecha_creacion": datetime(2026, 10, 24, 12, minuto, tzinfo=UTC),
        "cuenta_origen": "8712-4455",
        "cuenta_destino": "6033-9001",
        "monto_crc": 125_000,
        "moneda": "CRC",
        "cliente_nombre": "Maria <b>Rodriguez</b>",
        "reglas_disparadas": ["REG-001"],
    }


@pytest.fixture
def repo_alertas_falso() -> RepoAlertasFalso:
    # La media es la mas reciente y la critica la mas vieja: ordenar por fecha sola
    # las dejaria al reves.
    return RepoAlertasFalso(
        [_alerta(1, "CRITICA", 0), _alerta(2, "ALTA", 5), _alerta(3, "MEDIA", 10),
         _alerta(4, "CRITICA", 3)]
    )


@pytest.fixture
def repo_reglas() -> RepoReglasFalso:
    return RepoReglasFalso(reglas_como_documentos(), politica_como_documento())


@pytest.fixture
def aplicacion(repo_alertas_falso: RepoAlertasFalso, repo_reglas: RepoReglasFalso) -> FastAPI:
    creada = crear_aplicacion()
    creada.dependency_overrides[dep_repo_alertas] = lambda: repo_alertas_falso
    creada.dependency_overrides[dep_repo_reglas] = lambda: repo_reglas
    return creada


@pytest_asyncio.fixture
async def cliente(aplicacion: FastAPI) -> Any:
    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as http:
        yield http


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ruta", "esperado"),
    [
        ("/panel", "Panel de alertas"),
        ("/reglas", "Reglas de deteccion"),
        ("/detalle", "En construccion"),
        ("/busqueda", "Busqueda forense"),
        ("/tablero", "Tablero de indicadores"),
    ],
)
async def prueba_cada_pagina_responde_200_con_la_navegacion(
    cliente: AsyncClient, ruta: str, esperado: str
) -> None:
    respuesta = await cliente.get(ruta)
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/html")
    assert esperado in respuesta.text
    for destino in ("/panel", "/reglas", "/detalle", "/busqueda", "/tablero"):
        assert f'href="{destino}"' in respuesta.text
    assert "modo local" in respuesta.text
    assert "/estaticos/htmx.min.js" in respuesta.text
    assert "//unpkg" not in respuesta.text


@pytest.mark.asyncio
async def prueba_la_raiz_redirige_al_panel(cliente: AsyncClient) -> None:
    respuesta = await cliente.get("/", follow_redirects=False)
    assert respuesta.status_code in (302, 307)
    assert respuesta.headers["location"] == "/panel"


@pytest.mark.asyncio
async def prueba_el_panel_ordena_por_severidad_y_luego_fecha(cliente: AsyncClient) -> None:
    html = (await cliente.get("/panel")).text
    orden = re.findall(r'id="alerta-(ALR-\d+)"', html)
    # critica (mas reciente primero), alta, media
    assert orden == ["ALR-004", "ALR-001", "ALR-002", "ALR-003"]


@pytest.mark.asyncio
async def prueba_el_panel_se_conecta_al_canal_y_se_resincroniza(cliente: AsyncClient) -> None:
    html = (await cliente.get("/panel")).text
    assert 'sse-connect="/api/v1/alertas/flujo"' in html
    assert 'sse-swap="alerta"' in html
    assert 'hx-trigger="sse:resincronizar"' in html
    assert 'hx-get="/panel/cola"' in html
    assert "/estaticos/htmx-ext-sse.js" in html


@pytest.mark.asyncio
async def prueba_la_fila_se_formatea_y_escapa(cliente: AsyncClient) -> None:
    html = (await cliente.get("/panel")).text
    assert "125 000 CRC" in html
    assert "2026-10-24 06:00" in html  # 12:00 UTC es 06:00 en Costa Rica
    assert "<b>Rodriguez" not in html
    assert "&lt;b&gt;Rodriguez" in html


@pytest.mark.asyncio
async def prueba_la_cola_es_un_fragmento_con_las_mismas_filas(cliente: AsyncClient) -> None:
    respuesta = await cliente.get("/panel/cola")
    assert respuesta.status_code == 200
    assert "<html" not in respuesta.text
    assert re.findall(r'id="alerta-(ALR-\d+)"', respuesta.text) == [
        "ALR-004", "ALR-001", "ALR-002", "ALR-003",
    ]


@pytest.mark.asyncio
async def prueba_la_cola_respeta_el_tope_de_pagina(
    cliente: AsyncClient, repo_alertas_falso: RepoAlertasFalso
) -> None:
    await cliente.get("/panel")
    assert repo_alertas_falso.llamadas[-1]["limite"] <= 200


@pytest.mark.asyncio
async def prueba_reglas_muestra_catalogo_tipos_y_politica(cliente: AsyncClient) -> None:
    html = (await cliente.get("/reglas")).text
    for codigo in ("R-01", "R-03", "R-05", "R-07", "R-09", "R-11"):
        assert codigo in html
    assert "lista_riesgo" in html and "red_mulas" in html  # tipos soportados
    assert "Politica vigente" in html and "CRITICA" in html
    assert "Versiones anteriores (0)" in html
    assert 'name="version_esperada" value="1"' in html


def _formulario(**campos: str) -> dict[str, str]:
    base = {"umbral": "10", "peso": "35", "activa": "true", "parametros": "{}",
            "version_esperada": "1"}
    return {**base, **campos}


@pytest.mark.asyncio
async def prueba_editar_sube_la_version_y_guarda_la_anterior(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    respuesta = await cliente.post("/reglas/REG-001", data=_formulario(umbral="12", peso="40"))
    assert respuesta.status_code == 200
    assert "ahora es la version 2" in respuesta.text
    assert "Versiones anteriores (1)" in respuesta.text
    guardada = await repo_reglas.obtener("REG-001")
    assert guardada is not None
    assert (guardada["version"], guardada["umbral"], guardada["peso"]) == (2, 12, 40)
    assert guardada["versiones_anteriores"][0]["umbral"] == 10
    assert 'name="version_esperada" value="2"' in respuesta.text


@pytest.mark.asyncio
async def prueba_editar_con_version_vieja_muestra_el_409(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    await cliente.post("/reglas/REG-001", data=_formulario(umbral="12"))
    respuesta = await cliente.post("/reglas/REG-001", data=_formulario(umbral="99"))
    assert respuesta.status_code == 409
    assert "409" in respuesta.text and "cambio mientras se editaba" in respuesta.text
    assert "version actual: 2" in respuesta.text
    guardada = await repo_reglas.obtener("REG-001")
    assert guardada is not None and guardada["umbral"] == 12 and guardada["version"] == 2


@pytest.mark.asyncio
async def prueba_editar_con_carrera_en_el_repositorio_tambien_da_409(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    repo_reglas.interferir_antes_de_editar = True
    respuesta = await cliente.post("/reglas/REG-001", data=_formulario(umbral="12"))
    assert respuesta.status_code == 409
    assert "cambio mientras se editaba" in respuesta.text


@pytest.mark.asyncio
async def prueba_apagar_una_regla(cliente: AsyncClient, repo_reglas: RepoReglasFalso) -> None:
    respuesta = await cliente.post("/reglas/REG-001", data=_formulario(activa="false"))
    assert respuesta.status_code == 200
    guardada = await repo_reglas.obtener("REG-001")
    assert guardada is not None and guardada["activa"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "campos",
    [
        {"parametros": "{no es json"},
        {"parametros": '{"dias_historial": 5}'},  # monto no lee ese parametro
        {"peso": "250"},
        {"umbral": "abc"},
    ],
)
async def prueba_datos_invalidos_dan_422_visible(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso, campos: dict[str, str]
) -> None:
    respuesta = await cliente.post("/reglas/REG-001", data=_formulario(**campos))
    assert respuesta.status_code == 422
    assert "mensaje-error" in respuesta.text
    assert repo_reglas.escrituras == 0


@pytest.mark.asyncio
async def prueba_editar_una_regla_inexistente_da_404(cliente: AsyncClient) -> None:
    assert (await cliente.post("/reglas/REG-999", data=_formulario())).status_code == 404
