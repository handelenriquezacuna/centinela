"""H-25: cada ruta contra cada rol, generado desde la tabla de `app/seguridad/permisos.py`.

No hay una segunda lista de rutas en esta prueba: se recorren las de la aplicacion y las
de la tabla. Si alguien agrega una ruta sin decidir su permiso, `prueba_toda_ruta_tiene_
decision_en_la_tabla` falla; si deja una entrada de una ruta que ya no existe, tambien.

Lo negado se prueba por HTTP de punta a punta (el guardia responde antes de que corra
nada, asi que no hace falta ninguna base). Lo permitido se prueba contra el guardia
mismo: ejecutar cada ruta permitida pediria a Mongo datos que esta prueba no sembro, y lo
que aqui se afirma es la decision de acceso, no la respuesta de la ruta.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.api.sesion import AccesoProhibido, SesionRequerida, guardia
from app.main import crear_aplicacion
from app.modelos.alertas import RolAgente
from app.seguridad.permisos import (
    ACCESOS,
    PERMISOS_POR_ROL,
    Acceso,
    es_superficie_api,
    permite,
    resolver_acceso,
    rol_tiene,
)
from pruebas.sesiones import encabezados_de

EJEMPLOS = {
    "alerta_id": "ALR-001",
    "regla_id": "REG-001",
    "cuenta": "8712-4455",
    "propuesta": "6033-9002",
    "objetivo": "6033-9002",
    "riesgo_id": "RSK-001",
}
ROLES = list(RolAgente)


def _concreta(ruta: str) -> str:
    for nombre, valor in EJEMPLOS.items():
        ruta = ruta.replace("{" + nombre + "}", valor)
    return ruta


def _rutas_de(router: Any, prefijo: str = "") -> list[tuple[str, str]]:
    """(metodo, plantilla) de toda ruta de FastAPI, incluidas las ocultas del esquema."""
    encontradas: list[tuple[str, str]] = []
    for ruta in router.routes:
        incluida = getattr(ruta, "original_router", None)
        if incluida is not None:
            encontradas += _rutas_de(incluida, prefijo + ruta.include_context.prefix)
        elif isinstance(ruta, APIRoute):
            encontradas += [(m, prefijo + ruta.path) for m in sorted(ruta.methods) if m != "HEAD"]
    return encontradas


def _peticion(app: FastAPI, metodo: str, ruta: str, encabezados: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": metodo,
            "path": ruta,
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in encabezados.items()],
            "app": app,
            "state": {},
        }
    )


@pytest_asyncio.fixture
async def anonimo(aplicacion: FastAPI) -> AsyncIterator[AsyncClient]:
    """Cliente sin sesion (el `cliente` comun lleva la de un administrador)."""
    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as http:
        yield http


# ---------------------------------------------------------------------------
# La tabla cubre exactamente las rutas
# ---------------------------------------------------------------------------


def prueba_toda_ruta_tiene_decision_en_la_tabla() -> None:
    publicadas = set(_rutas_de(crear_aplicacion().router))
    decididas = {(a.metodo, a.ruta) for a in ACCESOS}

    assert publicadas - decididas == set(), "ruta sin permiso decidido (se negaria con 403)"
    assert decididas - publicadas == set(), "entrada de la tabla sin ruta"


def prueba_la_tabla_no_repite_rutas() -> None:
    pares = [(a.metodo, a.ruta) for a in ACCESOS]
    assert len(pares) == len(set(pares))


def prueba_la_ruta_mas_especifica_gana() -> None:
    assert resolver_acceso("GET", "/api/v1/reglas/tipos").ruta == "/api/v1/reglas/tipos"
    assert resolver_acceso("GET", "/api/v1/reglas/REG-001").ruta == "/api/v1/reglas/{regla_id}"
    assert resolver_acceso("GET", "/no-existe") is None


def prueba_sin_sesion_solo_es_publico_login_salud_y_lo_necesario_para_entrar() -> None:
    publicas = {(a.metodo, a.ruta) for a in ACCESOS if a.permiso is None}

    assert publicas == {
        ("GET", "/"),
        ("GET", "/login"),
        ("POST", "/login"),
        ("POST", "/salir"),
        ("GET", "/salud"),
        ("POST", "/api/v1/sesion"),
    }


# ---------------------------------------------------------------------------
# Negado: respuesta HTTP real
# ---------------------------------------------------------------------------

PROTEGIDAS = [a for a in ACCESOS if a.permiso is not None]


def _id(acceso: Acceso) -> str:
    return f"{acceso.metodo} {acceso.ruta}"


@pytest.mark.asyncio
@pytest.mark.parametrize("acceso", PROTEGIDAS, ids=_id)
async def prueba_sin_sesion_la_api_da_401_y_el_portal_redirige_al_login(
    anonimo: AsyncClient, acceso: Acceso
) -> None:
    ruta = _concreta(acceso.ruta)
    respuesta = await anonimo.request(acceso.metodo, ruta)

    if es_superficie_api(ruta):
        assert respuesta.status_code == 401
        assert respuesta.headers["www-authenticate"] == "Bearer"
        assert respuesta.json()["error"]["codigo"] == "no_autenticado"
        assert set(respuesta.json()["error"]) == {"codigo", "mensaje", "detalles"}
    else:
        assert respuesta.status_code == 303
        assert respuesta.headers["location"].startswith("/login")


NEGADAS = [
    pytest.param(acceso, rol, id=f"{rol.value}-{_id(acceso)}")
    for acceso in PROTEGIDAS
    for rol in ROLES
    if not rol_tiene(rol, acceso.permiso)
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("acceso", "rol"), NEGADAS)
async def prueba_cada_ruta_contra_cada_rol_niega_con_403_lo_que_no_le_toca(
    cliente: AsyncClient, acceso: Acceso, rol: RolAgente
) -> None:
    ruta = _concreta(acceso.ruta)
    respuesta = await cliente.request(acceso.metodo, ruta, headers=encabezados_de(rol))

    assert respuesta.status_code == 403
    if es_superficie_api(ruta):
        assert respuesta.json()["error"]["codigo"] == "prohibido"
    else:
        assert "Acceso denegado" in respuesta.text


# ---------------------------------------------------------------------------
# Permitido: decision del guardia
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("rol", ROLES, ids=lambda r: r.value)
@pytest.mark.parametrize("acceso", ACCESOS, ids=_id)
async def prueba_cada_ruta_contra_cada_rol_el_guardia_decide_segun_la_tabla(
    aplicacion: FastAPI, acceso: Acceso, rol: RolAgente
) -> None:
    ruta = _concreta(acceso.ruta)
    peticion = _peticion(aplicacion, acceso.metodo, ruta, encabezados_de(rol))
    debe_pasar = acceso.permiso is None or rol_tiene(rol, acceso.permiso)

    if debe_pasar:
        await guardia(peticion)
    else:
        with pytest.raises(AccesoProhibido):
            await guardia(peticion)
    assert permite(rol, acceso.metodo, ruta) is debe_pasar


@pytest.mark.asyncio
@pytest.mark.parametrize("acceso", ACCESOS, ids=_id)
async def prueba_el_guardia_sin_sesion_solo_deja_pasar_lo_publico(
    aplicacion: FastAPI, acceso: Acceso
) -> None:
    peticion = _peticion(aplicacion, acceso.metodo, _concreta(acceso.ruta), {})

    if acceso.permiso is None:
        await guardia(peticion)
    else:
        with pytest.raises(SesionRequerida):
            await guardia(peticion)


@pytest.mark.asyncio
async def prueba_una_ruta_fuera_de_la_tabla_se_niega(aplicacion: FastAPI) -> None:
    peticion = _peticion(aplicacion, "GET", "/ruta-nueva", encabezados_de())

    with pytest.raises(AccesoProhibido):
        await guardia(peticion)


# ---------------------------------------------------------------------------
# Lo que dice la historia, rol por rol
# ---------------------------------------------------------------------------


def prueba_permisos_de_cada_rol_segun_la_historia() -> None:
    def puede(rol: RolAgente, metodo: str, ruta: str) -> bool:
        return permite(rol, metodo, ruta)

    for ruta in ("/panel", "/detalle/ALR-001", "/busqueda", "/red-mulas/8712-4455"):
        assert puede(RolAgente.AGENTE, "GET", ruta)
    assert puede(RolAgente.AGENTE, "POST", "/detalle/ALR-001/transicion")
    for ruta in ("/tablero", "/reglas", "/api/v1/efectividad", "/api/v1/politica"):
        assert not puede(RolAgente.AGENTE, "GET", ruta)
    assert not puede(RolAgente.AGENTE, "PATCH", "/api/v1/politica")

    for metodo, ruta in (("GET", "/tablero"), ("PATCH", "/api/v1/reglas/REG-001"),
                         ("PATCH", "/api/v1/politica"), ("POST", "/reglas/REG-001")):
        assert puede(RolAgente.SUPERVISOR, metodo, ruta)

    assert PERMISOS_POR_ROL[RolAgente.ADMINISTRADOR] >= PERMISOS_POR_ROL[RolAgente.SUPERVISOR]
    assert all(permite(RolAgente.ADMINISTRADOR, a.metodo, _concreta(a.ruta)) for a in ACCESOS)
