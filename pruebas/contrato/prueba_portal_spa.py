"""La SPA servida por FastAPI y su sesion (T-61). Sin Mongo y sin construir la SPA.

- `portal.interfaz`: `spa` por omision (T-67); las rutas de pagina devuelven el
  `index.html` del build, el guardia decide igual que siempre y los fragmentos HTMX siguen
  vivos mientras dure el respaldo `htmx` (lo retira T-68).
- La SPA entra por `POST /api/v1/sesion`, que deja la MISMA cookie HttpOnly y
  SameSite=Strict que `/login`; pregunta quien es con `GET /api/v1/sesion` y sale con
  `DELETE /api/v1/sesion`. Nunca necesita el token en JavaScript.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.dependencias import dep_repo_alertas, dep_repo_casos_destino
from app.api.rutas import spa
from app.api.rutas.acceso import dep_repo_agentes
from app.main import crear_aplicacion
from app.nucleo.config import Ajustes
from app.seguridad.permisos import resolver_acceso
from pruebas.dobles.casos_demo import RepoCasosFalso
from pruebas.dobles.repo_agentes_falso import CONTRASENA, RepoAgentesFalso
from pruebas.dobles.repos_falsos import RepoAlertasFalso
from pruebas.sesiones import encabezados_de

INDICE = '<!doctype html><html lang="es"><div id="raiz"></div><!-- spa de prueba --></html>'


@pytest.fixture
def build_spa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `index.html` falso en lugar del build real: la prueba no depende de npm."""
    (tmp_path / "index.html").write_text(INDICE, encoding="utf-8")
    monkeypatch.setattr(spa, "RUTA_SPA", tmp_path)
    return tmp_path


def _app(interfaz: str) -> FastAPI:
    creada = crear_aplicacion(interfaz)
    alertas = RepoAlertasFalso([])
    creada.dependency_overrides[dep_repo_alertas] = lambda: alertas
    creada.dependency_overrides[dep_repo_casos_destino] = lambda: RepoCasosFalso(alertas)
    creada.dependency_overrides[dep_repo_agentes] = lambda: RepoAgentesFalso()
    return creada


@pytest_asyncio.fixture
async def http_spa(build_spa: Path) -> AsyncIterator[AsyncClient]:
    async with LifespanManager(_app("spa")) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as cliente:
        yield cliente


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------


def prueba_la_interfaz_por_omision_es_spa_y_el_entorno_vuelve_a_htmx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-67: la SPA es el portal por omision; HTMX queda a una variable de distancia."""
    monkeypatch.delenv("CENTINELA_PORTAL__INTERFAZ", raising=False)
    assert Ajustes().portal.interfaz == "spa"
    assert crear_aplicacion().state.interfaz == "spa"
    monkeypatch.setenv("CENTINELA_PORTAL__INTERFAZ", "htmx")
    assert Ajustes().portal.interfaz == "htmx"
    assert crear_aplicacion().state.interfaz == "htmx"
    monkeypatch.setenv("CENTINELA_PORTAL__INTERFAZ", "react")
    with pytest.raises(ValidationError):
        Ajustes()


# ---------------------------------------------------------------------------
# Las paginas devuelven la SPA
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plantilla", spa.PAGINAS_SPA)
def prueba_cada_pagina_de_la_spa_tiene_el_permiso_de_la_tabla(plantilla: str) -> None:
    """Las rutas de la SPA son las de pagina del portal: misma plantilla, mismo permiso."""
    acceso = resolver_acceso("GET", plantilla.replace("{alerta_id}", "ALR-001").replace("{cuenta}", "6033-9001"))
    assert acceso is not None and acceso.ruta == plantilla


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ruta",
    ["/", "/login", "/panel", "/detalle/ALR-001", "/red-mulas/6033-9001", "/busqueda", "/tablero", "/reglas"],
)
async def prueba_con_sesion_cada_pagina_devuelve_el_index_de_la_spa(
    http_spa: AsyncClient, ruta: str
) -> None:
    respuesta = await http_spa.get(ruta, headers=encabezados_de())

    assert respuesta.status_code == 200
    assert respuesta.text == INDICE
    assert respuesta.headers["content-type"].startswith("text/html")
    assert respuesta.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def prueba_sin_sesion_la_pagina_redirige_al_login_con_su_destino(
    http_spa: AsyncClient,
) -> None:
    respuesta = await http_spa.get("/detalle/ALR-001", follow_redirects=False)

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/login?siguiente=/detalle/ALR-001"


@pytest.mark.asyncio
async def prueba_un_rol_sin_permiso_recibe_la_spa_con_403(http_spa: AsyncClient) -> None:
    respuesta = await http_spa.get("/tablero", headers=encabezados_de("agente"))

    assert respuesta.status_code == 403
    assert respuesta.text == INDICE


@pytest.mark.asyncio
async def prueba_la_api_y_los_fragmentos_htmx_siguen_iguales(http_spa: AsyncClient) -> None:
    api = await http_spa.get("/api/v1/alertas", headers=encabezados_de())
    assert api.status_code == 200 and api.headers["content-type"] == "application/json"
    sin_sesion = await http_spa.get("/api/v1/alertas")
    assert sin_sesion.status_code == 401
    assert sin_sesion.json()["error"]["codigo"] == "no_autenticado"
    fragmento = await http_spa.get("/panel/cola", headers=encabezados_de())
    assert fragmento.status_code == 200 and fragmento.text != INDICE
    assert (await http_spa.get("/salud")).status_code == 200


@pytest.mark.asyncio
async def prueba_sin_build_la_pagina_explica_como_construirla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spa, "RUTA_SPA", tmp_path / "no-existe")
    async with AsyncClient(
        transport=ASGITransport(app=_app("spa")), base_url="http://pruebas"
    ) as cliente:
        respuesta = await cliente.get("/panel", headers=encabezados_de())

    assert respuesta.status_code == 503
    assert "python tareas.py front" in respuesta.text
    assert "CENTINELA_PORTAL__INTERFAZ=htmx" in respuesta.text


@pytest.mark.asyncio
async def prueba_sin_build_un_rol_sin_permiso_sigue_recibiendo_403(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una negacion no se disfraza de caida: sin build el 403 sigue siendo 403."""
    monkeypatch.setattr(spa, "RUTA_SPA", tmp_path / "no-existe")
    async with AsyncClient(
        transport=ASGITransport(app=_app("spa")), base_url="http://pruebas"
    ) as cliente:
        respuesta = await cliente.get("/tablero", headers=encabezados_de("agente"))

    assert respuesta.status_code == 403


@pytest.mark.asyncio
async def prueba_con_htmx_las_paginas_siguen_siendo_jinja(build_spa: Path) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app("htmx")), base_url="http://pruebas"
    ) as cliente:
        respuesta = await cliente.get("/panel", headers=encabezados_de())

    assert respuesta.status_code == 200
    assert respuesta.text != INDICE and "Panel de alertas" in respuesta.text


# ---------------------------------------------------------------------------
# Sesion de la SPA: cookie, quien soy y salir
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_login_de_la_api_deja_la_misma_cookie_que_el_portal(
    http_spa: AsyncClient,
) -> None:
    respuesta = await http_spa.post(
        "/api/v1/sesion", json={"agente_id": "AGT-001", "contrasena": CONTRASENA}
    )

    assert respuesta.status_code == 200
    cookie = respuesta.headers["set-cookie"].lower()
    assert cookie.startswith("centinela_sesion=")
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    # Con la cookie (sin Bearer), la sesion ya vale para la API y para las paginas.
    quien = await http_spa.get("/api/v1/sesion")
    assert quien.status_code == 200 and quien.json()["agente_id"] == "AGT-001"
    assert (await http_spa.get("/panel")).status_code == 200


@pytest.mark.asyncio
async def prueba_credenciales_malas_no_dejan_cookie_y_dan_401_en_la_forma_del_error(
    http_spa: AsyncClient,
) -> None:
    respuesta = await http_spa.post(
        "/api/v1/sesion", json={"agente_id": "AGT-001", "contrasena": "no-es-esta"}
    )

    assert respuesta.status_code == 401
    assert "set-cookie" not in respuesta.headers
    assert respuesta.json()["error"]["codigo"] == "no_autenticado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rol", "navegacion", "permisos_clave"),
    [
        ("agente", ["/panel", "/busqueda", "/red-mulas"], {"panel", "resolver"}),
        (
            "supervisor",
            ["/panel", "/busqueda", "/red-mulas", "/tablero", "/reglas"],
            {"tablero", "gestionar_listas"},
        ),
        (
            "administrador",
            [
                "/panel",
                "/busqueda",
                "/red-mulas",
                "/tablero",
                "/reglas",
                "/administracion/salud",
                "/administracion/respaldos",
                "/administracion/usuarios",
            ],
            {"administracion", "tablero"},
        ),
    ],
)
async def prueba_la_sesion_dice_rol_permisos_y_pantallas(
    http_spa: AsyncClient, rol: str, navegacion: list[str], permisos_clave: set[str]
) -> None:
    respuesta = await http_spa.get("/api/v1/sesion", headers=encabezados_de(rol, "AGT-007"))

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["agente_id"] == "AGT-007" and cuerpo["rol"] == rol
    assert [p["ruta"] for p in cuerpo["navegacion"]] == navegacion
    assert permisos_clave <= set(cuerpo["permisos"])
    assert "token" not in cuerpo
    assert ("/tablero" in cuerpo["paginas"]) is (rol != "agente")
    assert ("/administracion/salud" in cuerpo["paginas"]) is (rol == "administrador")
    assert "/detalle/{alerta_id}" in cuerpo["paginas"] and "/login" in cuerpo["paginas"]


@pytest.mark.asyncio
async def prueba_sin_sesion_quien_soy_es_401(http_spa: AsyncClient) -> None:
    respuesta = await http_spa.get("/api/v1/sesion")
    assert respuesta.status_code == 401
    assert respuesta.json()["error"]["codigo"] == "no_autenticado"


@pytest.mark.asyncio
async def prueba_salir_borra_la_cookie_y_es_idempotente(http_spa: AsyncClient) -> None:
    await http_spa.post("/api/v1/sesion", json={"agente_id": "AGT-001", "contrasena": CONTRASENA})

    salida = await http_spa.delete("/api/v1/sesion")

    assert salida.status_code == 204
    cookie = salida.headers["set-cookie"].lower()
    assert "centinela_sesion=" in cookie and "max-age=0" in cookie
    assert (await http_spa.get("/api/v1/sesion")).status_code == 401
    assert (await http_spa.delete("/api/v1/sesion")).status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rol", "inicio"),
    [("agente", "/panel"), ("supervisor", "/tablero"), ("administrador", "/administracion/salud")],
)
async def prueba_la_sesion_dice_donde_aterriza_cada_rol_y_el_nombre_del_agente(
    http_spa: AsyncClient, rol: str, inicio: str
) -> None:
    respuesta = await http_spa.get("/api/v1/sesion", headers=encabezados_de(rol, "AGT-001"))

    cuerpo = respuesta.json()
    assert cuerpo["inicio"] == inicio
    assert cuerpo["nombre"] == "Jose Solis"  # el nombre del agente en la base, no el id
    assert {p["area"] for p in cuerpo["navegacion"]} <= {"Alertas", "Supervisión", "Administración"}


@pytest.mark.asyncio
async def prueba_un_agente_que_ya_no_existe_conserva_la_sesion_sin_nombre(
    http_spa: AsyncClient,
) -> None:
    respuesta = await http_spa.get("/api/v1/sesion", headers=encabezados_de("agente", "AGT-777"))

    assert respuesta.status_code == 200 and respuesta.json()["nombre"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", ["/administracion", "/administracion/salud", "/administracion/respaldos", "/administracion/usuarios"])
async def prueba_administracion_es_solo_del_administrador(http_spa: AsyncClient, ruta: str) -> None:
    sin_sesion = await http_spa.get(ruta, follow_redirects=False)
    assert sin_sesion.status_code == 303 and sin_sesion.headers["location"].startswith("/login")
    for rol in ("agente", "supervisor"):
        negada = await http_spa.get(ruta, headers=encabezados_de(rol))
        assert negada.status_code == 403 and negada.text == INDICE
    permitida = await http_spa.get(ruta, headers=encabezados_de("administrador"))
    assert permitida.status_code == 200 and permitida.text == INDICE


# ---------------------------------------------------------------------------
# Una direccion desconocida
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_una_pagina_desconocida_con_sesion_devuelve_la_spa_con_404(
    http_spa: AsyncClient,
) -> None:
    respuesta = await http_spa.get("/no-existe/en-el-portal", headers=encabezados_de())

    assert respuesta.status_code == 404
    assert respuesta.text == INDICE and respuesta.headers["content-type"].startswith("text/html")


@pytest.mark.asyncio
async def prueba_una_pagina_desconocida_sin_sesion_lleva_al_login(http_spa: AsyncClient) -> None:
    respuesta = await http_spa.get("/no-existe", follow_redirects=False)

    assert respuesta.status_code == 303 and respuesta.headers["location"].startswith("/login")


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", ["/api/v1/no-existe", "/estaticos/no-existe.js", "/estado/x"])
async def prueba_la_api_y_los_estaticos_desconocidos_siguen_dando_el_404_json(
    http_spa: AsyncClient, ruta: str
) -> None:
    respuesta = await http_spa.get(ruta, headers=encabezados_de())

    assert respuesta.status_code == 404
    assert respuesta.json()["error"]["codigo"] == "no_encontrado"


@pytest.mark.asyncio
async def prueba_un_404_de_una_ruta_que_existe_no_se_disfraza_de_pagina(
    http_spa: AsyncClient,
) -> None:
    respuesta = await http_spa.get("/api/v1/alertas/ALR-999/detalle", headers=encabezados_de())

    assert respuesta.status_code == 404 and respuesta.json()["error"]["codigo"] == "no_encontrado"
