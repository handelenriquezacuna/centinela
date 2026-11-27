"""T-83 contra Mongo de verdad: el contador atomico, el validador y el login real bloqueado."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
from app.seguridad.bloqueo import calcular_fallo
from app.seguridad.usuarios import asignar_credencial, desbloquear_cuenta
from pruebas.conftest import BASE_SEMILLA
from pruebas.dobles.repo_agentes_falso import RepoAgentesFalso

pytestmark = pytest.mark.mongo

CLAVE_BUENA = "contrasena-de-pruebas-1"
MENSAJE = (
    "Identificador o contraseña incorrectos, o demasiados intentos. "
    "Intenta de nuevo en unos minutos."
)


@pytest_asyncio.fixture
async def agentes(base_con_demo: Any) -> Any:
    """`agentes` con el validador REAL de la semilla (el que acepta los campos de T-83)."""
    validadores = await obtener_validadores(base_con_demo.client[BASE_SEMILLA])
    await recrear_con_validador(base_con_demo, "agentes", validadores["agentes"])
    repo = RepoAgentes(base_con_demo)
    for agente_id, rol in (("AGT-001", "agente"), ("AGT-002", "administrador")):
        await base_con_demo["agentes"].insert_one(
            {"_id": agente_id, "nombre": agente_id, "rol": rol, "activo": True, "esquema_version": 1}
        )
        await asignar_credencial(repo, agente_id, CLAVE_BUENA)
    return base_con_demo


AHORA = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def prueba_el_validador_acepta_los_campos_y_rechaza_los_mal_tipados(agentes: Any) -> None:
    await agentes["agentes"].update_one(
        {"_id": "AGT-001"},
        {"$set": {"intentos_fallidos": 3, "bloqueado_hasta": AHORA}},
    )
    with pytest.raises(WriteError):
        await agentes["agentes"].update_one({"_id": "AGT-001"}, {"$set": {"intentos_fallidos": -1}})
    with pytest.raises(WriteError):
        await agentes["agentes"].update_one({"_id": "AGT-001"}, {"$set": {"bloqueado_hasta": "ayer"}})


@pytest.mark.asyncio
async def prueba_el_contador_atomico_suma_exactamente_con_intentos_concurrentes(
    agentes: Any,
) -> None:
    repo = RepoAgentes(agentes)

    resultados = await asyncio.gather(
        *(
            repo.registrar_fallo("AGT-001", maximo=1000, bloqueo_minutos=15, ahora=AHORA)
            for _ in range(60)
        )
    )

    documento = await agentes["agentes"].find_one({"_id": "AGT-001"})
    assert documento["intentos_fallidos"] == 60 and "bloqueado_hasta" not in documento
    assert sorted(r.intentos_fallidos for r in resultados if r) == list(range(1, 61))


@pytest.mark.asyncio
async def prueba_con_intentos_concurrentes_el_bloqueo_se_fija_una_sola_vez(agentes: Any) -> None:
    repo = RepoAgentes(agentes)

    resultados = await asyncio.gather(
        *(
            repo.registrar_fallo("AGT-001", maximo=5, bloqueo_minutos=15, ahora=AHORA)
            for _ in range(30)
        )
    )

    documento = await agentes["agentes"].find_one({"_id": "AGT-001"})
    # Pasado el quinto, el bloqueo vigente congela el contador: no se alarga ni se desborda.
    assert documento["intentos_fallidos"] == 5
    assert documento["bloqueado_hasta"] == AHORA + timedelta(minutes=15)
    assert sum(1 for r in resultados if r and r.bloqueo_nuevo) == 1


@pytest.mark.asyncio
async def prueba_la_base_y_el_doble_aplican_la_misma_regla(agentes: Any) -> None:
    """La actualizacion con pipeline repite `calcular_fallo`: se prueba paso a paso."""
    repo, doble = RepoAgentes(agentes), RepoAgentesFalso([{"_id": "AGT-001", "nombre": "x", "rol": "agente"}])
    momentos = [AHORA, AHORA, AHORA, AHORA, AHORA, AHORA + timedelta(minutes=1),
                AHORA + timedelta(minutes=16), AHORA + timedelta(minutes=16)]

    for ahora in momentos:
        real = await repo.registrar_fallo("AGT-001", maximo=5, bloqueo_minutos=15, ahora=ahora)
        esperado = await doble.registrar_fallo("AGT-001", maximo=5, bloqueo_minutos=15, ahora=ahora)
        assert real is not None and esperado is not None
        assert (real.intentos_fallidos, real.bloqueado_hasta, real.bloqueo_nuevo) == (
            esperado.intentos_fallidos, esperado.bloqueado_hasta, esperado.bloqueo_nuevo
        ), ahora
        # Lo que el pipeline ESCRIBIO en la base es lo que la regla en Python dice.
        guardado = await agentes["agentes"].find_one({"_id": "AGT-001"})
        assert guardado["intentos_fallidos"] == esperado.intentos_fallidos, ahora
        assert guardado.get("bloqueado_hasta") == esperado.bloqueado_hasta, ahora
    assert calcular_fallo({}, maximo=5, bloqueo_minutos=15, ahora=AHORA).intentos_fallidos == 1


@pytest.mark.asyncio
async def prueba_un_bloqueo_vencido_reinicia_el_conteo_en_la_base(agentes: Any) -> None:
    repo = RepoAgentes(agentes)
    for _ in range(5):
        await repo.registrar_fallo("AGT-001", maximo=5, bloqueo_minutos=15, ahora=AHORA)

    despues = await repo.registrar_fallo(
        "AGT-001", maximo=5, bloqueo_minutos=15, ahora=AHORA + timedelta(minutes=20)
    )

    assert despues is not None and despues.intentos_fallidos == 1 and despues.bloqueado_hasta is None
    documento = await agentes["agentes"].find_one({"_id": "AGT-001"})
    assert documento["intentos_fallidos"] == 1 and "bloqueado_hasta" not in documento


@pytest.mark.asyncio
async def prueba_reiniciar_desbloquear_y_cuenta_inexistente(agentes: Any) -> None:
    repo = RepoAgentes(agentes)
    for _ in range(5):
        await repo.registrar_fallo("AGT-001", maximo=5, bloqueo_minutos=15, ahora=AHORA)

    assert await repo.reiniciar_intentos("AGT-001") is True
    assert await repo.reiniciar_intentos("AGT-001") is False  # ya limpio
    await repo.registrar_fallo("AGT-001", maximo=1, bloqueo_minutos=15, ahora=AHORA)
    await desbloquear_cuenta(repo, "AGT-001")
    documento = await agentes["agentes"].find_one({"_id": "AGT-001"})
    assert "intentos_fallidos" not in documento and "bloqueado_hasta" not in documento
    assert await repo.registrar_fallo("AGT-099", maximo=5, bloqueo_minutos=15, ahora=AHORA) is None
    with pytest.raises(ValueError, match="No existe"):
        await desbloquear_cuenta(repo, "AGT-099")


@pytest.mark.asyncio
async def prueba_listar_trae_el_estado_y_nunca_la_credencial(agentes: Any) -> None:
    repo = RepoAgentes(agentes)
    await repo.registrar_fallo("AGT-001", maximo=5, bloqueo_minutos=15, ahora=AHORA)

    lista = await repo.listar()

    assert [a["_id"] for a in lista][:2] == ["AGT-001", "AGT-002"]
    uno = lista[0]
    assert uno["intentos_fallidos"] == 1 and uno["tiene_credencial"] is True
    assert all("credencial" not in a for a in lista)


@pytest_asyncio.fixture
async def http(ajustes: Ajustes, agentes: Any) -> AsyncIterator[AsyncClient]:
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


@pytest.mark.asyncio
async def prueba_login_real_cinco_fallos_bloquean_y_el_sexto_con_clave_buena_falla(
    http: AsyncClient, agentes: Any
) -> None:
    for _ in range(5):
        r = await http.post("/api/v1/sesion", json={"agente_id": "AGT-001", "contrasena": "mala"})
        assert r.status_code == 401 and r.json()["error"]["mensaje"] == MENSAJE

    sexto = await http.post(
        "/api/v1/sesion", json={"agente_id": "AGT-001", "contrasena": CLAVE_BUENA}
    )

    assert sexto.status_code == 401 and sexto.json()["error"]["mensaje"] == MENSAJE
    documento = await agentes["agentes"].find_one({"_id": "AGT-001"})
    assert documento["intentos_fallidos"] == 5 and documento["bloqueado_hasta"] > datetime.now(UTC)

    # El administrador lo ve en la lista y lo desbloquea; despues la clave buena entra.
    token = (
        await http.post("/api/v1/sesion", json={"agente_id": "AGT-002", "contrasena": CLAVE_BUENA})
    ).json()["token"]
    lista = await http.get("/api/v1/usuarios", headers={"Authorization": f"Bearer {token}"})
    assert lista.json()["bloqueados"] == 1
    await desbloquear_cuenta(RepoAgentes(agentes), "AGT-001")
    ok = await http.post("/api/v1/sesion", json={"agente_id": "AGT-001", "contrasena": CLAVE_BUENA})
    assert ok.status_code == 200
    assert "intentos_fallidos" not in await agentes["agentes"].find_one({"_id": "AGT-001"})


@pytest.mark.asyncio
async def prueba_renovar_con_mongo_real(http: AsyncClient) -> None:
    token = (
        await http.post("/api/v1/sesion", json={"agente_id": "AGT-001", "contrasena": CLAVE_BUENA})
    ).json()["token"]

    r = await http.post("/api/v1/sesion/renovar", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200 and r.json()["agente_id"] == "AGT-001"
