"""T-83: limite de intentos de login, renovar la sesion y la lista de usuarios (HTTP)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.rutas.acceso import dep_repo_agentes
from app.seguridad import tokens
from app.seguridad.usuarios import desbloquear_cuenta
from pruebas.dobles.repo_agentes_falso import CONTRASENA, RepoAgentesFalso
from pruebas.sesiones import CLAVE_DE_PRUEBAS, encabezados_de

MENSAJE = (
    "Identificador o contraseña incorrectos, o demasiados intentos. "
    "Intenta de nuevo en unos minutos."
)


@pytest.fixture
def agentes() -> RepoAgentesFalso:
    return RepoAgentesFalso()


async def _cliente(aplicacion: FastAPI, agentes: RepoAgentesFalso) -> AsyncIterator[AsyncClient]:
    aplicacion.dependency_overrides[dep_repo_agentes] = lambda: agentes
    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as cliente:
        yield cliente


@pytest_asyncio.fixture
async def http(aplicacion: FastAPI, agentes: RepoAgentesFalso) -> AsyncIterator[AsyncClient]:
    async for cliente in _cliente(aplicacion, agentes):
        yield cliente


def _cuerpo(agente_id: str = "AGT-001", contrasena: str = CONTRASENA) -> dict[str, str]:
    return {"agente_id": agente_id, "contrasena": contrasena}


async def _fallar(http: AsyncClient, veces: int, agente_id: str = "AGT-001") -> None:
    for _ in range(veces):
        respuesta = await http.post("/api/v1/sesion", json=_cuerpo(agente_id, "mala-clave"))
        assert respuesta.status_code == 401


# ---------------------------------------------------------------------------
# Bloqueo por cuenta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_cinco_fallos_bloquean_y_el_sexto_con_la_clave_buena_tambien_falla(
    http: AsyncClient, agentes: RepoAgentesFalso
) -> None:
    await _fallar(http, 5)

    respuesta = await http.post("/api/v1/sesion", json=_cuerpo())

    assert respuesta.status_code == 401
    assert respuesta.json()["error"]["mensaje"] == MENSAJE
    assert "set-cookie" not in respuesta.headers
    assert agentes.agentes["AGT-001"]["bloqueado_hasta"] > datetime.now(UTC)


@pytest.mark.asyncio
async def prueba_el_mensaje_es_identico_para_clave_mala_cuenta_inexistente_y_bloqueo(
    http: AsyncClient,
) -> None:
    await _fallar(http, 5, "AGT-002")
    cuerpos = [
        _cuerpo("AGT-001", "mala"),  # clave mala
        _cuerpo("AGT-099", "mala"),  # no existe
        _cuerpo("AGT-002", "mala"),  # bloqueada
        _cuerpo("AGT-002"),  # bloqueada, con la clave buena
    ]

    respuestas = [await http.post("/api/v1/sesion", json=c) for c in cuerpos]

    assert {r.status_code for r in respuestas} == {401}
    assert {r.text for r in respuestas} == {respuestas[0].text}
    assert respuestas[0].json()["error"]["mensaje"] == MENSAJE


@pytest.mark.asyncio
async def prueba_el_login_del_portal_da_el_mismo_mensaje_y_estado_cuando_la_cuenta_esta_bloqueada(
    http: AsyncClient,
) -> None:
    await _fallar(http, 5)

    respuesta = await http.post(
        "/login", data={"agente_id": "AGT-001", "contrasena": CONTRASENA}, follow_redirects=False
    )

    assert respuesta.status_code == 401
    assert "demasiados intentos" in respuesta.text
    assert "set-cookie" not in respuesta.headers


@pytest.mark.asyncio
async def prueba_un_acierto_antes_del_limite_reinicia_la_cuenta(
    http: AsyncClient, agentes: RepoAgentesFalso
) -> None:
    await _fallar(http, 4)
    assert (await http.post("/api/v1/sesion", json=_cuerpo())).status_code == 200
    await _fallar(http, 4)

    assert (await http.post("/api/v1/sesion", json=_cuerpo())).status_code == 200
    assert "bloqueado_hasta" not in agentes.agentes["AGT-001"]


@pytest.mark.asyncio
async def prueba_el_bloqueo_de_una_cuenta_no_afecta_a_las_demas(http: AsyncClient) -> None:
    await _fallar(http, 5, "AGT-001")

    assert (await http.post("/api/v1/sesion", json=_cuerpo("AGT-002"))).status_code == 200


@pytest.mark.asyncio
async def prueba_pasado_el_plazo_la_cuenta_entra_de_nuevo(
    http: AsyncClient, agentes: RepoAgentesFalso
) -> None:
    await _fallar(http, 5)
    agentes.agentes["AGT-001"]["bloqueado_hasta"] = datetime.now(UTC) - timedelta(seconds=1)

    assert (await http.post("/api/v1/sesion", json=_cuerpo())).status_code == 200


@pytest.mark.asyncio
async def prueba_el_administrador_desbloquea_con_la_herramienta(
    http: AsyncClient, agentes: RepoAgentesFalso
) -> None:
    await _fallar(http, 5)
    assert (await http.post("/api/v1/sesion", json=_cuerpo())).status_code == 401

    await desbloquear_cuenta(agentes, "AGT-001")

    assert (await http.post("/api/v1/sesion", json=_cuerpo())).status_code == 200


@pytest.mark.asyncio
async def prueba_desbloquear_un_agente_que_no_existe_es_un_error_claro(
    agentes: RepoAgentesFalso,
) -> None:
    with pytest.raises(ValueError, match="No existe el agente AGT-099"):
        await desbloquear_cuenta(agentes, "AGT-099")


@pytest.mark.asyncio
async def prueba_el_limite_y_el_plazo_salen_de_la_configuracion(
    aplicacion: FastAPI, agentes: RepoAgentesFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENTINELA_SESION__INTENTOS_MAXIMOS", "2")
    monkeypatch.setenv("CENTINELA_SESION__BLOQUEO_MINUTOS", "30")
    async for cliente in _cliente(aplicacion, agentes):
        await _fallar(cliente, 2)

    bloqueo = agentes.agentes["AGT-001"]["bloqueado_hasta"]
    assert timedelta(minutes=29) < bloqueo - datetime.now(UTC) <= timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Limite por IP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_probar_muchas_cuentas_inexistentes_frena_a_la_ip(
    aplicacion: FastAPI, agentes: RepoAgentesFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENTINELA_SESION__INTENTOS_POR_IP", "3")
    async for cliente in _cliente(aplicacion, agentes):
        for numero in range(3):
            await cliente.post("/api/v1/sesion", json=_cuerpo(f"AGT-9{numero}9", "x"))

        respuesta = await cliente.post("/api/v1/sesion", json=_cuerpo())

    assert respuesta.status_code == 401
    assert respuesta.json()["error"]["mensaje"] == MENSAJE


@pytest.mark.asyncio
async def prueba_los_aciertos_no_frenan_a_la_ip(
    aplicacion: FastAPI, agentes: RepoAgentesFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENTINELA_SESION__INTENTOS_POR_IP", "2")
    async for cliente in _cliente(aplicacion, agentes):
        for _ in range(6):
            assert (await cliente.post("/api/v1/sesion", json=_cuerpo())).status_code == 200


@pytest.mark.asyncio
async def prueba_la_auditoria_deja_fallo_y_bloqueo(
    http: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="centinela.auditoria"):
        await _fallar(http, 5)

    lineas = [r.getMessage() for r in caplog.records]
    assert sum(1 for linea in lineas if linea.startswith("login_fallido")) == 5
    assert sum(1 for linea in lineas if linea.startswith("bloqueo agente=AGT-001")) == 1


# ---------------------------------------------------------------------------
# Renovar la sesion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_renovar_con_sesion_valida_emite_token_nuevo_y_la_misma_cookie(
    http: AsyncClient,
) -> None:
    entrada = await http.post("/api/v1/sesion", json=_cuerpo("AGT-002"))
    viejo = entrada.json()
    http.cookies.clear()
    clave = CLAVE_DE_PRUEBAS.encode()
    antiguo, _ = tokens.emitir(
        clave, "AGT-002", "supervisor", minutos=2, ahora=datetime.now(UTC) - timedelta(minutes=1)
    )
    http.cookies.set("centinela_sesion", antiguo)

    respuesta = await http.post("/api/v1/sesion/renovar")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert (cuerpo["agente_id"], cuerpo["rol"]) == ("AGT-002", "supervisor")
    assert datetime.fromisoformat(cuerpo["expira"]) > datetime.now(UTC) + timedelta(minutes=25)
    assert cuerpo["token"] != antiguo and viejo["tipo"] == "Bearer"
    cookie = respuesta.headers["set-cookie"].lower()
    assert "centinela_sesion=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    sesion = tokens.verificar(clave, cuerpo["token"])
    assert sesion.agente_id == "AGT-002" and sesion.expira == datetime.fromisoformat(cuerpo["expira"])


@pytest.mark.asyncio
async def prueba_renovar_con_bearer_tambien_sirve(http: AsyncClient) -> None:
    respuesta = await http.post("/api/v1/sesion/renovar", headers=encabezados_de("agente", "AGT-001"))

    assert respuesta.status_code == 200 and respuesta.json()["rol"] == "agente"


@pytest.mark.asyncio
async def prueba_una_sesion_vencida_no_se_renueva(http: AsyncClient) -> None:
    vencido, _ = tokens.emitir(
        CLAVE_DE_PRUEBAS.encode(), "AGT-001", "agente", minutos=1,
        ahora=datetime.now(UTC) - timedelta(minutes=5),
    )

    respuesta = await http.post(
        "/api/v1/sesion/renovar", headers={"Authorization": f"Bearer {vencido}"}
    )

    assert respuesta.status_code == 401
    assert respuesta.json()["error"]["codigo"] == "no_autenticado"
    assert "set-cookie" not in respuesta.headers


@pytest.mark.asyncio
async def prueba_sin_sesion_no_se_renueva(http: AsyncClient) -> None:
    respuesta = await http.post("/api/v1/sesion/renovar")

    assert respuesta.status_code == 401


@pytest.mark.asyncio
async def prueba_no_se_renueva_a_quien_ya_no_puede_entrar(
    http: AsyncClient, agentes: RepoAgentesFalso
) -> None:
    encabezados = encabezados_de("agente", "AGT-001")
    agentes.agentes["AGT-001"]["activo"] = False
    assert (await http.post("/api/v1/sesion/renovar", headers=encabezados)).status_code == 401

    agentes.agentes["AGT-001"]["activo"] = True
    agentes.agentes["AGT-001"]["bloqueado_hasta"] = datetime.now(UTC) + timedelta(minutes=5)
    assert (await http.post("/api/v1/sesion/renovar", headers=encabezados)).status_code == 401


@pytest.mark.asyncio
async def prueba_renovar_aplica_el_rol_actual_de_la_cuenta(
    http: AsyncClient, agentes: RepoAgentesFalso
) -> None:
    agentes.agentes["AGT-001"]["rol"] = "supervisor"

    respuesta = await http.post("/api/v1/sesion/renovar", headers=encabezados_de("agente", "AGT-001"))

    assert respuesta.json()["rol"] == "supervisor"


# ---------------------------------------------------------------------------
# Usuarios (solo administrador, solo lectura)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_administrador_ve_las_cuentas_y_cuales_estan_bloqueadas(
    http: AsyncClient,
) -> None:
    await _fallar(http, 5, "AGT-002")
    await _fallar(http, 2, "AGT-001")

    respuesta = await http.get("/api/v1/usuarios", headers=encabezados_de("administrador", "AGT-003"))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    por_id = {u["agente_id"]: u for u in cuerpo["elementos"]}
    assert cuerpo["total"] == 5 and cuerpo["bloqueados"] == 1
    assert por_id["AGT-002"]["bloqueado"] and por_id["AGT-002"]["bloqueado_hasta"]
    assert por_id["AGT-002"]["intentos_fallidos"] == 5
    assert not por_id["AGT-001"]["bloqueado"] and por_id["AGT-001"]["intentos_fallidos"] == 2
    assert por_id["AGT-001"]["bloqueado_hasta"] is None
    assert por_id["AGT-005"]["tiene_credencial"] is False
    assert '"credencial"' not in respuesta.text and '"hash"' not in respuesta.text


@pytest.mark.asyncio
async def prueba_un_bloqueo_vencido_ya_no_figura_como_bloqueado(
    http: AsyncClient, agentes: RepoAgentesFalso
) -> None:
    agentes.agentes["AGT-001"]["intentos_fallidos"] = 5
    agentes.agentes["AGT-001"]["bloqueado_hasta"] = datetime.now(UTC) - timedelta(minutes=1)

    cuerpo = (await http.get("/api/v1/usuarios", headers=encabezados_de("administrador"))).json()

    assert cuerpo["bloqueados"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("rol", ["agente", "supervisor"])
async def prueba_agente_y_supervisor_no_ven_los_usuarios(http: AsyncClient, rol: str) -> None:
    respuesta = await http.get("/api/v1/usuarios", headers=encabezados_de(rol))

    assert respuesta.status_code == 403 and respuesta.json()["error"]["codigo"] == "prohibido"


@pytest.mark.asyncio
async def prueba_sin_sesion_los_usuarios_dan_401(http: AsyncClient) -> None:
    assert (await http.get("/api/v1/usuarios")).status_code == 401
