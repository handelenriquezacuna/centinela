"""H-25: iniciar y cerrar sesion, cookie, token Bearer, redirecciones y el SSE."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.rutas.acceso import dep_repo_agentes
from app.seguridad import tokens
from pruebas.dobles.repo_agentes_falso import CONTRASENA, RepoAgentesFalso
from pruebas.sesiones import CLAVE_DE_PRUEBAS, encabezados_de

MENSAJE = (
    "Identificador o contraseña incorrectos, o demasiados intentos. "
    "Intenta de nuevo en unos minutos."
)


@pytest.fixture
def agentes() -> RepoAgentesFalso:
    return RepoAgentesFalso()


@pytest_asyncio.fixture
async def http(aplicacion: FastAPI, agentes: RepoAgentesFalso) -> AsyncIterator[AsyncClient]:
    """Cliente SIN sesion, contra la app con el repo de agentes en memoria."""
    aplicacion.dependency_overrides[dep_repo_agentes] = lambda: agentes
    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as cliente:
        yield cliente


def _formulario(agente_id: str = "AGT-001", contrasena: str = CONTRASENA, **extra: str) -> dict:
    return {"agente_id": agente_id, "contrasena": contrasena, **extra}


# ---------------------------------------------------------------------------
# API: token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agente_id", "rol"),
    [("AGT-001", "agente"), ("AGT-002", "supervisor"), ("AGT-003", "administrador")],
)
async def prueba_el_login_de_la_api_devuelve_un_token_con_el_rol(
    http: AsyncClient, agente_id: str, rol: str
) -> None:
    respuesta = await http.post("/api/v1/sesion", json=_formulario(agente_id))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["rol"] == rol and cuerpo["agente_id"] == agente_id and cuerpo["tipo"] == "Bearer"
    sesion = tokens.verificar(CLAVE_DE_PRUEBAS.encode(), cuerpo["token"])
    assert (sesion.agente_id, sesion.rol.value) == (agente_id, rol)


@pytest.mark.asyncio
async def prueba_el_token_del_login_sirve_como_bearer(http: AsyncClient) -> None:
    token = (await http.post("/api/v1/sesion", json=_formulario())).json()["token"]

    respuesta = await http.get("/api/v1/alertas", headers={"Authorization": f"Bearer {token}"})

    assert respuesta.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        _formulario(contrasena="no-es-esta-contrasena"),
        _formulario(agente_id="AGT-099"),
        _formulario(agente_id="AGT-004"),  # inactivo
        _formulario(agente_id="AGT-005"),  # sin credencial
    ],
    ids=["contrasena-mala", "no-existe", "inactivo", "sin-credencial"],
)
async def prueba_credenciales_malas_dan_401_con_el_mismo_mensaje(
    http: AsyncClient, cuerpo: dict
) -> None:
    respuesta = await http.post("/api/v1/sesion", json=cuerpo)

    assert respuesta.status_code == 401
    assert respuesta.json()["error"]["codigo"] == "no_autenticado"
    assert respuesta.json()["error"]["mensaje"] == MENSAJE


@pytest.mark.asyncio
async def prueba_un_cuerpo_mal_formado_es_422_con_la_forma_unica(http: AsyncClient) -> None:
    respuesta = await http.post("/api/v1/sesion", json={"agente_id": "Jose"})

    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["codigo"] == "parametros_invalidos"


@pytest.mark.asyncio
async def prueba_un_token_expirado_o_alterado_da_401(http: AsyncClient) -> None:
    clave = CLAVE_DE_PRUEBAS.encode()
    expirado, _ = tokens.emitir(
        clave, "AGT-001", "agente", minutos=1, ahora=datetime.now(UTC) - timedelta(minutes=5)
    )
    valido, _ = tokens.emitir(clave, "AGT-001", "agente", minutos=5)
    alterado = valido[:-2] + ("AA" if valido[-2:] != "AA" else "BB")

    for token in (expirado, alterado, "basura"):
        respuesta = await http.get("/api/v1/alertas", headers={"Authorization": f"Bearer {token}"})
        assert respuesta.status_code == 401, token


@pytest.mark.asyncio
async def prueba_el_canal_sse_exige_sesion(http: AsyncClient) -> None:
    respuesta = await http.get("/api/v1/alertas/flujo")

    assert respuesta.status_code == 401
    assert respuesta.json()["error"]["codigo"] == "no_autenticado"


@pytest.mark.asyncio
async def prueba_openapi_exige_sesion_y_docs_ya_no_existe(http: AsyncClient) -> None:
    assert (await http.get("/openapi.json")).status_code == 401
    ok = await http.get("/openapi.json", headers=encabezados_de("agente"))
    assert ok.status_code == 200 and "/api/v1/sesion" in ok.json()["paths"]


@pytest.mark.asyncio
async def prueba_salud_es_publica(http: AsyncClient) -> None:
    # Sin base arriba responde 503, pero nunca 401: el guardia la deja pasar.
    assert (await http.get("/salud")).status_code != 401


# ---------------------------------------------------------------------------
# Portal: cookie
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_pantalla_de_login_es_publica_y_trae_el_formulario(http: AsyncClient) -> None:
    respuesta = await http.get("/login")

    assert respuesta.status_code == 200
    assert 'name="agente_id"' in respuesta.text and 'type="password"' in respuesta.text
    assert "/salir" not in respuesta.text  # sin sesion no hay boton de salir


@pytest.mark.asyncio
async def prueba_el_login_del_portal_deja_la_cookie_y_vuelve_a_donde_iba(
    http: AsyncClient,
) -> None:
    respuesta = await http.post(
        "/login", data=_formulario(siguiente="/busqueda?q=1"), follow_redirects=False
    )

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/busqueda?q=1"
    cookie = respuesta.headers["set-cookie"].lower()
    assert "centinela_sesion=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "secure" not in cookie  # por omision: el portal local corre por HTTP
    assert (await http.get("/panel")).status_code == 200  # la cookie quedo en el cliente


@pytest.mark.asyncio
async def prueba_la_cookie_es_secure_cuando_se_configura(
    aplicacion: FastAPI, agentes: RepoAgentesFalso, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENTINELA_SESION__COOKIE_SEGURA", "true")
    aplicacion.dependency_overrides[dep_repo_agentes] = lambda: agentes
    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as cliente:
        respuesta = await cliente.post("/login", data=_formulario(), follow_redirects=False)

    assert "secure" in respuesta.headers["set-cookie"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "siguiente",
    [
        "//otro.sitio/x",
        "https://otro.sitio",
        "/\\otro.sitio",
        "javascript:alert(1)",
        "/login",
        # El navegador quita TAB y saltos de linea: quedarian en //otro.sitio.
        "/\t/otro.sitio",
        "/\n/otro.sitio",
        "/ /otro.sitio",
        "/panel\\..\\//otro.sitio",
    ],
)
async def prueba_siguiente_no_puede_sacar_al_usuario_del_portal(
    http: AsyncClient, siguiente: str
) -> None:
    respuesta = await http.post(
        "/login", data=_formulario(siguiente=siguiente), follow_redirects=False
    )

    assert respuesta.headers["location"] == "/panel"


@pytest.mark.asyncio
async def prueba_credenciales_malas_en_el_portal_muestran_el_mensaje_sin_cookie(
    http: AsyncClient,
) -> None:
    respuesta = await http.post("/login", data=_formulario(contrasena="mala"))

    assert respuesta.status_code == 401
    assert MENSAJE in respuesta.text and "AGT-001" in respuesta.text
    assert "set-cookie" not in respuesta.headers


@pytest.mark.asyncio
async def prueba_sin_sesion_el_portal_manda_al_login_recordando_donde_iba(
    http: AsyncClient,
) -> None:
    respuesta = await http.get("/detalle/ALR-001", follow_redirects=False)

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/login?siguiente=/detalle/ALR-001"


@pytest.mark.asyncio
async def prueba_una_peticion_htmx_sin_sesion_pide_redirigir(http: AsyncClient) -> None:
    respuesta = await http.get("/panel/cola", headers={"HX-Request": "true"})

    assert respuesta.status_code == 401
    assert respuesta.headers["hx-redirect"] == "/login"


@pytest.mark.asyncio
async def prueba_una_cookie_alterada_no_da_sesion(http: AsyncClient) -> None:
    valido, _ = tokens.emitir(CLAVE_DE_PRUEBAS.encode(), "AGT-001", "agente", minutos=5)
    http.cookies.set("centinela_sesion", valido[:-2] + "AA")

    respuesta = await http.get("/panel", follow_redirects=False)

    assert respuesta.status_code == 303


@pytest.mark.asyncio
async def prueba_salir_borra_la_cookie_y_cierra_el_acceso(http: AsyncClient) -> None:
    await http.post("/login", data=_formulario())
    assert (await http.get("/panel")).status_code == 200

    salida = await http.post("/salir", follow_redirects=False)

    assert salida.status_code == 303 and salida.headers["location"] == "/login"
    assert (await http.get("/panel", follow_redirects=False)).status_code == 303


@pytest.mark.asyncio
async def prueba_la_navegacion_muestra_solo_lo_que_el_rol_puede_abrir(
    http: AsyncClient,
) -> None:
    como_agente = (await http.get("/panel", headers=encabezados_de("agente"))).text
    como_supervisor = (await http.get("/panel", headers=encabezados_de("supervisor"))).text

    assert 'href="/busqueda"' in como_agente and 'href="/red-mulas"' in como_agente
    assert 'href="/tablero"' not in como_agente and 'href="/reglas"' not in como_agente
    assert 'href="/tablero"' in como_supervisor and 'href="/reglas"' in como_supervisor
    assert "(agente)" in como_agente and "(supervisor)" in como_supervisor


@pytest.mark.asyncio
async def prueba_un_agente_en_el_tablero_ve_acceso_denegado_con_403(http: AsyncClient) -> None:
    respuesta = await http.get("/tablero", headers=encabezados_de("agente"))

    assert respuesta.status_code == 403
    assert "Acceso denegado" in respuesta.text


# ---------------------------------------------------------------------------
# /estado y la clave
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_sin_clave_en_dev_se_genera_una_y_estado_advierte(
    aplicacion: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CENTINELA_SESION__CLAVE")
    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as cliente:
        # La clave de pruebas ya no es la del servidor: su token no sirve, y el proceso
        # arranco igual con una clave propia.
        assert (await cliente.get("/estado", headers=encabezados_de())).status_code == 401
        servicio = aplicacion.state.servicio_sesion
        token, _ = servicio.emitir("AGT-001", "agente")
        respuesta = await cliente.get("/estado", headers={"Authorization": f"Bearer {token}"})

    assert respuesta.status_code == 200
    avisos = respuesta.json()["advertencias"]
    assert len(avisos) == 1 and "CENTINELA_SESION__CLAVE" in avisos[0]


@pytest.mark.asyncio
async def prueba_con_clave_estado_no_advierte(http: AsyncClient) -> None:
    respuesta = await http.get("/estado", headers=encabezados_de("agente"))

    assert respuesta.status_code == 200 and respuesta.json()["advertencias"] == []
