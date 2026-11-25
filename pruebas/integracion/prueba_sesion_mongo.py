"""H-25 contra Mongo de verdad: credencial en `agentes`, login real y agente de la sesion."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pymongo.errors import WriteError

from app.main import crear_aplicacion
from app.nucleo.config import Ajustes, obtener_ajustes
from app.repos.agentes import RepoAgentes
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.seguridad import contrasenas
from app.seguridad.usuarios import asignar_credencial
from pruebas.conftest import BASE_SEMILLA
from pruebas.dobles.repo_agentes_falso import PARAMETROS_BARATOS

pytestmark = pytest.mark.mongo

CLAVE_BUENA = "contrasena-de-pruebas-1"


@pytest_asyncio.fixture
async def agentes_reales(base_con_demo: Any) -> Any:
    """`agentes` con el validador REAL de la semilla (el que acepta o no la credencial)."""
    validadores = await obtener_validadores(base_con_demo.client[BASE_SEMILLA])
    await recrear_con_validador(base_con_demo, "agentes", validadores["agentes"])
    repo = RepoAgentes(base_con_demo)
    for agente_id, nombre, rol in (
        ("AGT-001", "Jose Solis", "agente"),
        ("AGT-002", "Laura Mendez", "agente"),
    ):
        await base_con_demo["agentes"].insert_one(
            {"_id": agente_id, "nombre": nombre, "rol": rol, "activo": True, "esquema_version": 1}
        )
        await asignar_credencial(repo, agente_id, CLAVE_BUENA)
    return base_con_demo


@pytest_asyncio.fixture
async def http(ajustes: Ajustes, agentes_reales: Any) -> AsyncIterator[AsyncClient]:
    os.environ["CENTINELA_MONGO__BASE"] = ajustes.mongo.base
    obtener_ajustes.cache_clear()
    try:
        async with LifespanManager(crear_aplicacion("htmx")) as gestionada, AsyncClient(
            transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
        ) as cliente:
            yield cliente
    finally:
        os.environ.pop("CENTINELA_MONGO__BASE", None)
        obtener_ajustes.cache_clear()


async def _token(http: AsyncClient, agente_id: str, contrasena: str = CLAVE_BUENA) -> str:
    respuesta = await http.post(
        "/api/v1/sesion", json={"agente_id": agente_id, "contrasena": contrasena}
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["token"]


# ---------------------------------------------------------------------------
# El validador y la semilla
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_validador_real_acepta_una_credencial_y_rechaza_una_mal_formada(
    agentes_reales: Any,
) -> None:
    documento = await agentes_reales["agentes"].find_one({"_id": "AGT-001"})
    assert set(documento["credencial"]) == {"hash", "sal", "parametros"}

    sin_sal = {k: v for k, v in documento["credencial"].items() if k != "sal"}
    with pytest.raises(WriteError):
        await agentes_reales["agentes"].update_one({"_id": "AGT-001"}, {"$set": {"credencial": sin_sal}})
    con_extra = {**documento["credencial"], "contrasena": "x"}
    with pytest.raises(WriteError):
        await agentes_reales["agentes"].update_one({"_id": "AGT-001"}, {"$set": {"credencial": con_extra}})
    await agentes_reales["agentes"].update_one(
        {"_id": "AGT-001"},
        {"$set": {"credencial": contrasenas.cifrar("otra-contrasena-1", PARAMETROS_BARATOS)}},
    )


@pytest.mark.asyncio
async def prueba_la_semilla_deja_a_los_agentes_sin_credencial_y_con_el_rol_administrador(
    base_mongo: Any,
) -> None:
    semilla = base_mongo.client[BASE_SEMILLA]
    assert await semilla["agentes"].count_documents({"credencial": {"$exists": True}}) == 0
    assert await semilla["agentes"].count_documents({}) >= 1
    validador = (await obtener_validadores(semilla))["agentes"]
    assert "administrador" in validador["$jsonSchema"]["properties"]["rol"]["enum"]


@pytest.mark.asyncio
async def prueba_asignar_credencial_crea_cambia_el_rol_y_exige_un_agente_existente(
    agentes_reales: Any,
) -> None:
    repo = RepoAgentes(agentes_reales)

    await asignar_credencial(repo, "AGT-002", "una-contrasena-nueva-1", rol=None)
    from app.modelos.alertas import RolAgente

    await asignar_credencial(repo, "AGT-002", "una-contrasena-nueva-2", RolAgente.SUPERVISOR)
    asignado = await repo.obtener("AGT-002")
    assert asignado["rol"] == "supervisor"
    assert contrasenas.verificar("una-contrasena-nueva-2", asignado["credencial"])
    assert not contrasenas.verificar("una-contrasena-nueva-1", asignado["credencial"])

    with pytest.raises(ValueError, match="No existe"):
        await asignar_credencial(repo, "AGT-099", "una-contrasena-nueva-1")
    with pytest.raises(ValueError, match="al menos"):
        await asignar_credencial(repo, "AGT-001", "corta")


# ---------------------------------------------------------------------------
# Login real
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_login_real_y_lectura_con_el_token(http: AsyncClient) -> None:
    token = await _token(http, "AGT-001")

    respuesta = await http.get("/api/v1/alertas", headers={"Authorization": f"Bearer {token}"})

    assert respuesta.status_code == 200 and respuesta.json()["total"] >= 1


@pytest.mark.asyncio
async def prueba_login_real_rechaza_contrasena_mala_y_agente_inexistente(
    http: AsyncClient,
) -> None:
    for agente_id, contrasena in (("AGT-001", "no-es-la-contrasena"), ("AGT-099", CLAVE_BUENA)):
        respuesta = await http.post(
            "/api/v1/sesion", json={"agente_id": agente_id, "contrasena": contrasena}
        )
        assert respuesta.status_code == 401


@pytest.mark.asyncio
async def prueba_un_agente_desactivado_no_inicia_sesion(
    http: AsyncClient, agentes_reales: Any
) -> None:
    await agentes_reales["agentes"].update_one({"_id": "AGT-002"}, {"$set": {"activo": False}})

    respuesta = await http.post(
        "/api/v1/sesion", json={"agente_id": "AGT-002", "contrasena": CLAVE_BUENA}
    )

    assert respuesta.status_code == 401


@pytest.mark.asyncio
async def prueba_el_login_del_portal_real_abre_el_panel_y_el_rol_limita(
    http: AsyncClient,
) -> None:
    entrada = await http.post(
        "/login", data={"agente_id": "AGT-001", "contrasena": CLAVE_BUENA}, follow_redirects=False
    )
    assert entrada.status_code == 303
    assert (await http.get("/panel")).status_code == 200
    assert (await http.get("/tablero")).status_code == 403  # AGT-001 es agente


@pytest.mark.asyncio
async def prueba_el_historial_registra_al_agente_de_la_sesion_no_al_del_cliente(
    http: AsyncClient, agentes_reales: Any
) -> None:
    token = await _token(http, "AGT-001")

    respuesta = await http.post(
        "/api/v1/alertas/ALR-002/transiciones",
        json={"destino": "en_revision", "agente_id": "AGT-002"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert respuesta.status_code == 200, respuesta.text
    alerta = await agentes_reales["alertas"].find_one({"_id": "ALR-002"})
    assert alerta["agente_id"] == "AGT-001"
    assert alerta["historial"][-1]["agente_id"] == "AGT-001"
