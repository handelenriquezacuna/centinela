"""T-82 · El endpoint PUBLICO del cliente: `GET/POST /api/v1/cliente/{token}`.

El repositorio es un doble: aqui se prueba el contrato HTTP. Con Mongo de verdad (la transaccion
que gasta el enlace y escribe la alerta) lo prueba `integracion/prueba_aviso_cliente_mongo.py`.

Lo que no puede fallar: un enlace inexistente, vencido sin responder y mal formado dan LA MISMA
respuesta; responder gasta el enlace; la alerta no se cierra sola; el limite corta los intentos.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_repo_respuesta_cliente
from app.repos.aviso_cliente import (
    AlertaDelEnlaceNoExiste,
    EnlaceNoDisponible,
    ProtocoloRepoRespuestaCliente,
)
from app.seguridad.enlaces import hash_de
from pruebas.sesiones import encabezados_de

TOKEN = "T" * 43
TOKEN_VENCIDO = "V" * 43
TOKEN_INEXISTENTE = "X" * 43
AHORA = datetime.now(UTC)


def _notificacion(token: str, *, vence: datetime, usado: datetime | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": "NOT-000001-SMS",
        "alerta_id": "ALR-000001",
        "canal": "sms",
        "enlace_hash": hash_de(token),
        "enlace_vence": vence,
        "fecha": AHORA,
        "estado_envio": "enviado",
        "destinatarios": ["8***-**55"],
        "texto": f"Banco: aviso. Responde aquí: http://127.0.0.1:8000/c/{token}",
    }
    if usado is not None:
        doc["enlace_usado_en"] = usado
    return doc


def _alerta(estado: str = "nueva") -> dict[str, Any]:
    return {
        "_id": "ALR-000001",
        "estado": estado,
        "monto_crc": 280_000,
        "fecha_creacion": AHORA - timedelta(minutes=1),
        "cuenta_origen": "8712-4455",
        "cuenta_destino": "6033-9001",
    }


class RepoClienteFalso:
    def __init__(self, estado_alerta: str = "nueva") -> None:
        self.notificaciones = {
            hash_de(TOKEN): _notificacion(TOKEN, vence=AHORA + timedelta(minutes=10)),
            hash_de(TOKEN_VENCIDO): _notificacion(TOKEN_VENCIDO, vence=AHORA - timedelta(minutes=1)),
        }
        self.alerta = _alerta(estado_alerta)
        self.respuestas: list[str] = []

    async def notificacion_por_hash(self, enlace_hash: str) -> dict[str, Any] | None:
        return self.notificaciones.get(enlace_hash)

    async def alerta_del_enlace(self, alerta_id: str) -> dict[str, Any] | None:
        return self.alerta if alerta_id == self.alerta["_id"] else None

    async def registrar_respuesta(self, enlace_hash: str, valor: str, ahora: datetime) -> dict[str, Any]:
        nota = self.notificaciones.get(enlace_hash)
        if nota is None or "enlace_usado_en" in nota or nota["enlace_vence"] <= ahora:
            raise EnlaceNoDisponible
        if self.alerta is None:  # pragma: no cover
            raise AlertaDelEnlaceNoExiste
        respuesta = {"valor": valor, "fecha": ahora}
        nota["enlace_usado_en"] = ahora
        nota["respuesta_cliente"] = respuesta
        self.alerta["respuesta_cliente"] = respuesta
        self.respuestas.append(valor)
        return nota

    async def bandeja_sms(self, limite: int) -> list[dict[str, Any]]:
        return list(self.notificaciones.values())[:limite]


@pytest.fixture
def repo(aplicacion: FastAPI) -> RepoClienteFalso:
    falso = RepoClienteFalso()
    aplicacion.dependency_overrides[dep_repo_respuesta_cliente] = lambda: falso
    return falso


@pytest_asyncio.fixture
async def anonimo(aplicacion: FastAPI, repo: RepoClienteFalso) -> AsyncIterator[AsyncClient]:
    """Sin sesion ni cookie: asi llega el cliente desde su telefono."""
    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as http:
        yield http


def prueba_el_doble_cumple_el_protocolo() -> None:
    assert isinstance(RepoClienteFalso(), ProtocoloRepoRespuestaCliente)


@pytest.mark.asyncio
async def prueba_un_enlace_valido_muestra_la_transferencia_sin_datos_sensibles(anonimo: AsyncClient) -> None:
    respuesta = await anonimo.get(f"/api/v1/cliente/{TOKEN}")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["fase"] == "pendiente" and cuerpo["puede_responder"] is True
    assert cuerpo["transferencia"]["monto_crc"] == 280_000
    assert cuerpo["transferencia"]["cuenta_terminada_en"] == "4455"
    assert cuerpo["transferencia"]["destino_enmascarado"] == "6033-****"
    assert cuerpo["caso"] is None and cuerpo["respuesta"] is None
    assert cuerpo["ayuda"] == {"telefono": "800-000-0000", "es_ejemplo": True}
    crudo = respuesta.text
    for prohibido in ("8712-4455", "6033-9001", "ALR-000001", "enlace_hash", "cliente_nombre"):
        assert prohibido not in crudo
    assert respuesta.headers["cache-control"] == "no-store"
    assert respuesta.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [TOKEN_VENCIDO, TOKEN_INEXISTENTE, "corto", "a b" * 20, "%2e%2e" * 10])
async def prueba_vencido_inexistente_o_mal_formado_dan_la_misma_respuesta(anonimo: AsyncClient, token: str) -> None:
    referencia = await anonimo.get(f"/api/v1/cliente/{TOKEN_INEXISTENTE}")
    respuesta = await anonimo.get(f"/api/v1/cliente/{token}")

    assert respuesta.status_code == 404 and referencia.status_code == 404
    assert respuesta.json() == referencia.json()
    cuerpo = respuesta.json()["error"]
    assert cuerpo["codigo"] == "no_encontrado"
    assert cuerpo["detalles"] == [{"campo": "telefono_ayuda", "mensaje": "800-000-0000"}]


@pytest.mark.asyncio
async def prueba_responder_no_fui_yo_gasta_el_enlace_y_no_cierra_la_alerta(
    anonimo: AsyncClient, repo: RepoClienteFalso
) -> None:
    respuesta = await anonimo.post(f"/api/v1/cliente/{TOKEN}/respuesta", json={"valor": "no_fui_yo"})

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["fase"] == "respondida" and cuerpo["puede_responder"] is False
    assert cuerpo["respuesta"]["valor"] == "no_fui_yo"
    assert cuerpo["caso"]["titulo"] == "Recibimos tu respuesta"
    assert repo.alerta["estado"] == "nueva"  # la alerta la decide el agente

    # El mismo enlace sigue sirviendo para VER el estado, y refleja lo que haga el agente.
    repo.alerta["estado"] = "en_revision"
    visto = (await anonimo.get(f"/api/v1/cliente/{TOKEN}")).json()
    assert visto["caso"]["titulo"] == "Un agente revisa tu caso"
    repo.alerta["estado"] = "confirmada"
    visto = (await anonimo.get(f"/api/v1/cliente/{TOKEN}")).json()
    assert visto["caso"]["titulo"] == "Confirmamos el fraude: llama al 800-000-0000"
    repo.alerta["estado"] = "descartada"
    visto = (await anonimo.get(f"/api/v1/cliente/{TOKEN}")).json()
    assert visto["caso"]["titulo"] == "Cerramos el caso: no hubo fraude"


@pytest.mark.asyncio
async def prueba_un_enlace_usado_no_admite_otra_respuesta(anonimo: AsyncClient, repo: RepoClienteFalso) -> None:
    await anonimo.post(f"/api/v1/cliente/{TOKEN}/respuesta", json={"valor": "si_fui_yo"})
    segunda = await anonimo.post(f"/api/v1/cliente/{TOKEN}/respuesta", json={"valor": "no_fui_yo"})

    assert segunda.status_code == 409
    assert segunda.json()["error"]["codigo"] == "conflicto"
    assert repo.respuestas == ["si_fui_yo"]


@pytest.mark.asyncio
async def prueba_responder_con_un_enlace_vencido_es_el_mismo_404(anonimo: AsyncClient, repo: RepoClienteFalso) -> None:
    respuesta = await anonimo.post(f"/api/v1/cliente/{TOKEN_VENCIDO}/respuesta", json={"valor": "no_fui_yo"})

    assert respuesta.status_code == 404
    assert repo.respuestas == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cuerpo", [{"valor": "quizas"}, {}, {"valor": None}])
async def prueba_una_respuesta_invalida_es_422(anonimo: AsyncClient, cuerpo: dict[str, Any]) -> None:
    respuesta = await anonimo.post(f"/api/v1/cliente/{TOKEN}/respuesta", json=cuerpo)

    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["codigo"] == "parametros_invalidos"


@pytest.mark.asyncio
async def prueba_con_el_caso_ya_cerrado_no_se_acepta_respuesta(
    anonimo: AsyncClient, repo: RepoClienteFalso
) -> None:
    repo.alerta["estado"] = "confirmada"
    visto = (await anonimo.get(f"/api/v1/cliente/{TOKEN}")).json()
    assert visto["puede_responder"] is False and visto["caso"]["codigo"] == "fraude_confirmado"

    respuesta = await anonimo.post(f"/api/v1/cliente/{TOKEN}/respuesta", json={"valor": "no_fui_yo"})
    assert respuesta.status_code == 409 and repo.respuestas == []


@pytest.mark.asyncio
async def prueba_probar_enlaces_al_azar_bloquea_a_la_ip_hasta_con_un_enlace_bueno(anonimo: AsyncClient) -> None:
    for i in range(10):  # intentos_invalidos_por_ip por omision
        assert (await anonimo.get(f"/api/v1/cliente/{'Z' * 42}{i}")).status_code == 404

    bloqueada = await anonimo.get(f"/api/v1/cliente/{TOKEN}")
    assert bloqueada.status_code == 429
    assert bloqueada.json()["error"]["codigo"] == "demasiados_intentos"


@pytest.mark.asyncio
async def prueba_insistir_con_el_mismo_enlace_se_limita(anonimo: AsyncClient, repo: RepoClienteFalso) -> None:
    estados = [
        (await anonimo.post(f"/api/v1/cliente/{TOKEN}/respuesta", json={"valor": "no_fui_yo"})).status_code
        for _ in range(7)
    ]
    # Una acepta; las demas son 409 hasta el tope de respuestas_por_enlace (5), y luego 429.
    assert estados == [200, 409, 409, 409, 409, 429, 429]
    assert repo.respuestas == ["no_fui_yo"]


@pytest.mark.asyncio
async def prueba_la_pagina_del_cliente_es_publica_y_no_cacheable(aplicacion: FastAPI) -> None:
    from app.main import crear_aplicacion

    spa = crear_aplicacion("spa")
    async with LifespanManager(spa) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as http:
        for token in (TOKEN, "cualquier-cosa"):  # la pagina no revela si el enlace existe
            respuesta = await http.get(f"/c/{token}")
            assert respuesta.status_code in (200, 503)  # 503 si falta el build de la SPA
            assert respuesta.headers["referrer-policy"] == "no-referrer"
            assert respuesta.headers["cache-control"] == "no-store"
            assert "location" not in respuesta.headers


@pytest.mark.asyncio
async def prueba_el_simulador_pide_rol_y_bandera(cliente: AsyncClient, repo: RepoClienteFalso) -> None:
    from app.modelos.alertas import RolAgente
    from pruebas.sesiones import encabezados_de

    agente = await cliente.get("/api/v1/simulador-sms", headers=encabezados_de(RolAgente.AGENTE))
    assert agente.status_code == 403
    supervisor = await cliente.get("/api/v1/simulador-sms", headers=encabezados_de(RolAgente.SUPERVISOR))
    assert supervisor.status_code == 200
    mensaje = supervisor.json()["mensajes"][0]
    assert mensaje["para"] == "8***-**55" and mensaje["ruta_enlace"].startswith("/c/")
    assert "enlace_hash" not in mensaje


@pytest.mark.asyncio
async def prueba_con_la_bandera_apagada_el_simulador_no_existe(
    aplicacion: FastAPI, monkeypatch: pytest.MonkeyPatch, repo: RepoClienteFalso
) -> None:
    from app.modelos.alertas import RolAgente
    from app.nucleo.config import obtener_ajustes

    monkeypatch.setenv("CENTINELA_AVISO_CLIENTE__SIMULADOR_SMS", "false")
    obtener_ajustes.cache_clear()
    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as http:
        respuesta = await http.get("/api/v1/simulador-sms", headers=encabezados_de(RolAgente.ADMINISTRADOR))
    obtener_ajustes.cache_clear()
    assert respuesta.status_code == 404
