"""H-11 · `GET /api/v1/notificaciones`: forma, paginacion, filtro y permisos.

El repositorio se sustituye por un doble: aqui se prueba el contrato HTTP. Contra Mongo
de verdad (el `$lookup` con la alerta) lo prueba `integracion/prueba_d2_mongo.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.dependencias import dep_repo_notificaciones
from app.repos.notificaciones import ProtocoloRepoLecturaNotificaciones
from pruebas.sesiones import encabezados_de

RUTA = "/api/v1/notificaciones"
BASE = datetime(2026, 9, 25, 4, 47, tzinfo=UTC)


def _documento(numero: int, estado: str = "enviado", *, con_alerta: bool = True) -> dict[str, Any]:
    documento: dict[str, Any] = {
        "_id": f"NOT-{numero:06d}",
        "alerta_id": f"ALR-{numero:06d}",
        "canal": "correo",
        "estado_envio": estado,
        "fecha": BASE + timedelta(minutes=numero),
        "intentos": 1,
        "asunto": f"[Centinela] Alerta crítica ALR-{numero:06d}",
        "destinatarios": ["guardia@centinela.local"],
        "esquema_version": 1,
        "alerta": [
            {"severidad": "CRITICA", "puntaje": 75, "monto_crc": 280_000, "cliente_nombre": "Maria Rodriguez Vargas"}
        ]
        if con_alerta
        else [],
    }
    if estado == "fallido":
        documento["detalle_error"] = "ConnectionRefusedError: [Errno 61] Connection refused"
    return documento


class RepoLecturaFalso:
    def __init__(self, documentos: list[dict[str, Any]]) -> None:
        self.documentos = documentos
        self.llamadas: list[dict[str, Any]] = []

    async def listar(
        self, *, estado_envio: str | None, limite: int, desplazamiento: int
    ) -> tuple[list[dict[str, Any]], int]:
        self.llamadas.append(
            {"estado_envio": estado_envio, "limite": limite, "desplazamiento": desplazamiento}
        )
        filtrados = [
            d for d in self.documentos if estado_envio is None or d["estado_envio"] == estado_envio
        ]
        filtrados.sort(key=lambda d: (d["fecha"], d["_id"]), reverse=True)
        return filtrados[desplazamiento : desplazamiento + limite], len(filtrados)


@pytest.fixture
def repo(aplicacion: FastAPI) -> RepoLecturaFalso:
    falso = RepoLecturaFalso(
        [_documento(1), _documento(2, "fallido"), _documento(3, "pendiente"), _documento(4, con_alerta=False)]
    )
    aplicacion.dependency_overrides[dep_repo_notificaciones] = lambda: falso
    return falso


def prueba_el_doble_cumple_el_protocolo() -> None:
    assert isinstance(RepoLecturaFalso([]), ProtocoloRepoLecturaNotificaciones)


@pytest.mark.asyncio
async def prueba_lista_la_pagina_con_la_forma_documentada(cliente: AsyncClient, repo: RepoLecturaFalso) -> None:
    respuesta = await cliente.get(RUTA, headers=encabezados_de("supervisor"))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert set(cuerpo) == {"total", "limite", "desplazamiento", "elementos"}
    assert cuerpo["total"] == 4
    assert cuerpo["desplazamiento"] == 0
    # Las mas recientes primero.
    assert [e["notificacion_id"] for e in cuerpo["elementos"]] == [
        "NOT-000004", "NOT-000003", "NOT-000002", "NOT-000001",
    ]
    primera = cuerpo["elementos"][3]
    assert primera == {
        "notificacion_id": "NOT-000001",
        "alerta_id": "ALR-000001",
        "canal": "correo",
        "estado_envio": "enviado",
        "fecha": "2026-09-25T04:48:00Z",
        "intentos": 1,
        "asunto": "[Centinela] Alerta crítica ALR-000001",
        "destinatarios": ["guardia@centinela.local"],
        "detalle_error": None,
        "severidad": "CRITICA",
        "puntaje": 75,
        "monto_crc": 280000,
        "cliente_nombre": "Maria Rodriguez Vargas",
    }
    assert isinstance(primera["monto_crc"], int), "colones enteros, nunca flotante"


@pytest.mark.asyncio
async def prueba_una_alerta_que_ya_no_existe_sale_sin_sus_datos(cliente: AsyncClient, repo: RepoLecturaFalso) -> None:
    cuerpo = (await cliente.get(RUTA, headers=encabezados_de("supervisor"))).json()

    huerfana = next(e for e in cuerpo["elementos"] if e["notificacion_id"] == "NOT-000004")
    assert huerfana["severidad"] is None
    assert huerfana["monto_crc"] is None
    assert huerfana["cliente_nombre"] is None


@pytest.mark.asyncio
async def prueba_la_fallida_trae_el_motivo(cliente: AsyncClient, repo: RepoLecturaFalso) -> None:
    cuerpo = (await cliente.get(RUTA, params={"estado_envio": "fallido"}, headers=encabezados_de("supervisor"))).json()

    assert cuerpo["total"] == 1
    assert cuerpo["elementos"][0]["detalle_error"].startswith("ConnectionRefusedError")
    assert repo.llamadas[-1]["estado_envio"] == "fallido"


@pytest.mark.asyncio
async def prueba_pagina_y_acota_el_limite(cliente: AsyncClient, repo: RepoLecturaFalso) -> None:
    pagina = (
        await cliente.get(RUTA, params={"limite": 2, "desplazamiento": 2}, headers=encabezados_de("supervisor"))
    ).json()
    assert pagina["total"] == 4
    assert (pagina["limite"], pagina["desplazamiento"]) == (2, 2)
    assert [e["notificacion_id"] for e in pagina["elementos"]] == ["NOT-000002", "NOT-000001"]

    acotada = (await cliente.get(RUTA, params={"limite": 100000}, headers=encabezados_de("supervisor"))).json()
    assert acotada["limite"] == 200, "se acota a app.pagina_maxima y la respuesta lo dice"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parametros",
    [{"estado_envio": "perdido"}, {"limite": 0}, {"desplazamiento": -1}],
)
async def prueba_un_parametro_invalido_es_422_en_la_forma_unica_del_error(
    cliente: AsyncClient, repo: RepoLecturaFalso, parametros: dict[str, Any]
) -> None:
    respuesta = await cliente.get(RUTA, params=parametros, headers=encabezados_de("supervisor"))

    assert respuesta.status_code == 422
    assert set(respuesta.json()["error"]) == {"codigo", "mensaje", "detalles"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("rol", "estado"), [("agente", 403), ("supervisor", 200), ("administrador", 200)])
async def prueba_solo_supervisor_y_administrador_leen_las_notificaciones(
    cliente: AsyncClient, repo: RepoLecturaFalso, rol: str, estado: int
) -> None:
    respuesta = await cliente.get(RUTA, headers=encabezados_de(rol))

    assert respuesta.status_code == estado
    if estado == 403:
        assert respuesta.json()["error"]["codigo"] == "prohibido"


@pytest.mark.asyncio
async def prueba_sin_sesion_es_401(aplicacion: FastAPI, repo: RepoLecturaFalso) -> None:
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as anonimo:
        respuesta = await anonimo.get(RUTA)

    assert respuesta.status_code == 401


@pytest.mark.asyncio
async def prueba_la_pagina_del_respaldo_htmx_muestra_la_misma_lista(
    cliente: AsyncClient, repo: RepoLecturaFalso
) -> None:
    respuesta = await cliente.get("/notificaciones", headers=encabezados_de("supervisor"))

    assert respuesta.status_code == 200
    html = respuesta.text
    assert "<h1>Notificaciones</h1>" in html
    assert "al menos una vez" in html
    assert "ALR-000001" in html and "₡280.000" in html and "Maria Rodriguez Vargas" in html
    assert "Fallido" in html and "ConnectionRefusedError" in html
    assert 'href="/notificaciones"' in html, "la entrada del menu del supervisor"


@pytest.mark.asyncio
async def prueba_el_menu_del_agente_no_trae_notificaciones(cliente: AsyncClient) -> None:
    respuesta = await cliente.get("/panel", headers=encabezados_de("agente"))

    assert respuesta.status_code == 200
    assert 'href="/notificaciones"' not in respuesta.text
