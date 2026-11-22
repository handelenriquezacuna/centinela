"""Pagina de detalle de alerta (H-18) con repositorios falsos. Sin Mongo."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_repo_alertas, dep_repo_detalle, dep_repo_reglas
from app.main import crear_aplicacion
from app.repos.detalle import ProtocoloRepoDetalle
from pruebas.dobles.catalogo_reglas import (
    politica_como_documento,
    reglas_como_documentos,
)
from pruebas.dobles.repo_detalle_falso import RepoDetalleFalso, alerta_v1, alerta_v2
from pruebas.dobles.repos_falsos import RepoAlertasFalso, RepoReglasFalso

Armar = Callable[[dict[str, Any]], Awaitable[tuple[AsyncClient, RepoAlertasFalso]]]


@pytest_asyncio.fixture
async def armar() -> Any:
    abiertos: list[Any] = []

    async def _armar(alerta: dict[str, Any]) -> tuple[AsyncClient, RepoAlertasFalso]:
        alertas = RepoAlertasFalso([alerta])
        creada = crear_aplicacion()
        creada.dependency_overrides[dep_repo_alertas] = lambda: alertas
        creada.dependency_overrides[dep_repo_detalle] = lambda: RepoDetalleFalso(alertas.casos)
        creada.dependency_overrides[dep_repo_reglas] = lambda: RepoReglasFalso(
            reglas_como_documentos(), politica_como_documento()
        )
        gestor = LifespanManager(creada)
        gestionada = await gestor.__aenter__()
        http = AsyncClient(
            transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
        )
        abiertos.append((gestor, http))
        return http, alertas

    yield _armar
    for gestor, http in abiertos:
        await http.aclose()
        await gestor.__aexit__(None, None, None)


def _botones(html: str) -> list[str]:
    return re.findall(r'data-destino="([a-z_]+)"', html)


def test_el_doble_cumple_el_protocolo() -> None:
    assert isinstance(RepoDetalleFalso(), ProtocoloRepoDetalle)


@pytest.mark.asyncio
async def prueba_v2_muestra_transaccion_perfil_reglas_y_la_suma(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    respuesta = await http.get("/detalle/ALR-001")
    assert respuesta.status_code == 200
    html = respuesta.text
    assert "750 000 CRC" in html and "8712-4455 (CTA-001)" in html and "6033-9001" in html
    assert "2026-10-14 22:47" in html  # hora de Costa Rica, no UTC
    assert "45 200 CRC" in html and "180 000 CRC" in html and "07:00 - 19:00" in html
    for fragmento in ("R-01", "REG-001", "monto 750000 supera 10 veces el promedio 45200",
                      "+35", "+25", "+15", "POL-001", "version 1"):
        assert fragmento in html
    # "no disponible: regla editada" se muestra tal cual.
    assert "no disponible: regla editada" in html
    assert re.search(r'id="suma-aportes">75<', html)
    assert 'id="puntaje-total">75' in html
    assert "aviso-v1" not in html
    # El corte alcanzado queda marcado: 75 cae en CRITICA (70-100).
    fila = re.search(r'<tr class="corte-alcanzado">\s*<td>(\w+)</td>', html)
    assert fila is not None and fila.group(1) == "CRITICA"


@pytest.mark.asyncio
async def prueba_v1_muestra_los_ids_y_el_aviso(armar: Armar) -> None:
    http, _ = await armar(alerta_v1())
    html = (await http.get("/detalle/ALR-001")).text
    assert "alerta v1: migrar con python tareas.py migrar" in html
    assert all(regla in html for regla in ("REG-001", "REG-002", "REG-003"))
    assert "tabla-aportes" in html  # la tabla de cortes sigue
    assert 'id="tabla-aportes"' not in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("estado", "esperados"),
    [
        ("nueva", ["en_revision"]),
        ("en_revision", ["confirmada", "descartada", "escalada"]),
        ("confirmada", []),
        ("descartada", []),
        ("escalada", []),
    ],
)
async def prueba_solo_aparecen_los_botones_que_la_maquina_permite(
    armar: Armar, estado: str, esperados: list[str]
) -> None:
    http, _ = await armar(alerta_v2(estado, None if estado == "nueva" else "AGT-001"))
    html = (await http.get("/detalle/ALR-001")).text
    assert _botones(html) == esperados
    assert ("estado terminal" in html) == (not esperados)


@pytest.mark.asyncio
async def prueba_el_selector_trae_los_agentes_de_la_coleccion(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    html = (await http.get("/detalle/ALR-001")).text
    assert "Jose Solis (AGT-001)" in html and "Laura Mendez (AGT-002)" in html


@pytest.mark.asyncio
async def prueba_alerta_inexistente_es_404(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    assert (await http.get("/detalle/ALR-099")).status_code == 404


@pytest.mark.asyncio
async def prueba_tomar_y_escalar_crea_el_caso_y_agrega_historial(armar: Armar) -> None:
    http, alertas = await armar(alerta_v2())
    tomada = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=en_revision&agente_id=AGT-001&nota=la+tomo&estado_esperado=nueva",
    )
    assert tomada.status_code == 200
    assert _botones(tomada.text) == ["confirmada", "descartada", "escalada"]
    assert 'hx-swap-oob="true"' in tomada.text and "la tomo" in tomada.text

    escalada = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=escalada&agente_id=AGT-001&estado_esperado=en_revision",
    )
    assert escalada.status_code == 200
    assert "CAS-001" in escalada.text and "caso-creado" in escalada.text
    assert "CAS-001" in alertas.casos
    # Recargar la pagina sigue mostrando el caso y el historial completo, en orden.
    html = (await http.get("/detalle/ALR-001")).text
    assert "CAS-001" in html
    acciones = re.findall(r"Accion: <strong>(\w+)</strong>", html)
    assert acciones == ["nueva", "en_revision", "escalada"]


@pytest.mark.asyncio
async def prueba_el_409_de_otro_agente_se_ve_como_mensaje(armar: Armar) -> None:
    http, alertas = await armar(alerta_v2())
    alertas.interferir_antes_de_transicionar = True  # otro agente la toma en el medio
    respuesta = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=en_revision&agente_id=AGT-001&estado_esperado=nueva",
    )
    assert respuesta.status_code == 409
    assert "mensaje-error" in respuesta.text and "409" in respuesta.text
    assert alertas.escrituras == 0


@pytest.mark.asyncio
async def prueba_el_409_de_estado_desactualizado_y_de_transicion_invalida(armar: Armar) -> None:
    http, _ = await armar(alerta_v2("en_revision", "AGT-002"))
    desactualizado = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=en_revision&agente_id=AGT-001&estado_esperado=nueva",
    )
    assert desactualizado.status_code == 409 and "ya no esta en nueva" in desactualizado.text
    # Muestra la alerta como esta ahora: en revision, de AGT-002.
    assert _botones(desactualizado.text) == ["confirmada", "descartada", "escalada"]
    invalida = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=nueva&agente_id=AGT-002&estado_esperado=en_revision",
    )
    assert invalida.status_code == 409 and "transicion invalida" in invalida.text
    ajeno = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=confirmada&agente_id=AGT-001&estado_esperado=en_revision",
    )
    assert ajeno.status_code == 409 and "AGT-002" in ajeno.text


@pytest.mark.asyncio
async def prueba_datos_invalidos_son_422_con_mensaje(armar: Armar) -> None:
    http, alertas = await armar(alerta_v2())
    respuesta = await http.post("/detalle/ALR-001/transicion", content="destino=&agente_id=x")
    assert respuesta.status_code == 422 and "mensaje-error" in respuesta.text
    assert alertas.escrituras == 0


@pytest.mark.asyncio
async def prueba_el_panel_enlaza_cada_fila_al_detalle(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    js = (await http.get("/estaticos/panel.js")).text
    assert '"/detalle/"' in js
