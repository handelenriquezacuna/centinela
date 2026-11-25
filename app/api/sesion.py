"""Sesion en HTTP: el guardia de permisos, sus errores y la identidad de la peticion.

Un solo guardia, instalado como dependencia de TODA la aplicacion. Consulta la tabla de
`app/seguridad/permisos.py` con el metodo y la ruta de la peticion; no cada ruta decide
por su cuenta, asi que no hay una ruta que se olvide de proteger a medias. Una ruta sin
entrada en la tabla se niega (403).

Donde viaja el token: cabecera `Authorization: Bearer ...` (API) o cookie HttpOnly
(portal). Si vienen las dos, gana la cabecera.

Respuestas ante el fallo:

- API (`/api/...`, `/estado`, `/openapi.json`): 401 o 403 con la forma unica del error.
- Portal: 401 -> redireccion a `/login?siguiente=...` (en una peticion HTMX, 401 con
  `HX-Redirect`); 403 -> pagina de acceso denegado (con `portal.interfaz: spa`, el
  `index.html` de la SPA con estado 403, y la SPA dibuja la pantalla).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.api.dependencias import dep_ajustes
from app.api.errores import CodigoError, cuerpo
from app.seguridad.permisos import (
    es_superficie_api,
    permite,
    resolver_acceso,
    rol_tiene,
)
from app.seguridad.servicio import ServicioSesion
from app.seguridad.tokens import Sesion

# La sesion de la peticion en curso, para las plantillas (que no reciben `Request`). Se
# fija en el guardia, que es `async` y corre en la misma tarea que el manejador de la
# ruta, de modo que el valor llega hasta el render.
_SESION_ACTUAL: ContextVar[Sesion | None] = ContextVar("centinela_sesion_actual", default=None)


class SesionRequerida(Exception):
    """No hay sesion valida (falta, esta alterada o expiro)."""


class AccesoProhibido(Exception):
    """Hay sesion, pero su rol no permite esta operacion."""


def sesion_actual() -> Sesion | None:
    return _SESION_ACTUAL.get()


def permite_ruta(ruta: str, metodo: str = "GET") -> bool:
    """Para las plantillas: si la sesion actual puede entrar a esa ruta."""
    sesion = sesion_actual()
    return permite(sesion.rol if sesion else None, metodo, ruta)


def dep_servicio_sesion(peticion: Request) -> ServicioSesion:
    servicio: ServicioSesion | None = getattr(peticion.app.state, "servicio_sesion", None)
    if servicio is None:
        # App armada sin lifespan (pruebas que no lo disparan): se construye una vez con
        # los ajustes, igual que haria el lifespan.
        servicio = ServicioSesion.desde_ajustes(dep_ajustes(peticion))
        peticion.app.state.servicio_sesion = servicio
    return servicio


ServicioSesionDep = Annotated[ServicioSesion, Depends(dep_servicio_sesion)]


def token_de(peticion: Request, servicio: ServicioSesion) -> str | None:
    autorizacion = peticion.headers.get("authorization", "")
    esquema, _, valor = autorizacion.partition(" ")
    if esquema.lower() == "bearer" and valor.strip():
        return valor.strip()
    return peticion.cookies.get(servicio.nombre_cookie)


async def guardia(peticion: Request) -> None:
    """Dependencia global: aplica la tabla de permisos a cada peticion."""
    servicio = dep_servicio_sesion(peticion)
    sesion = servicio.leer(token_de(peticion, servicio))
    _SESION_ACTUAL.set(sesion)
    peticion.state.sesion = sesion

    acceso = resolver_acceso(peticion.method, peticion.url.path)
    if acceso is None:
        raise AccesoProhibido  # falla cerrada: ruta sin decision en la tabla
    if acceso.permiso is None:
        return
    if sesion is None:
        raise SesionRequerida
    if not rol_tiene(sesion.rol, acceso.permiso):
        raise AccesoProhibido


async def dep_sesion(peticion: Request) -> Sesion:
    """La sesion de la peticion; las rutas que la usan ya pasaron por el guardia."""
    sesion: Sesion | None = getattr(peticion.state, "sesion", None)
    if sesion is None:
        raise SesionRequerida
    return sesion


SesionDep = Annotated[Sesion, Depends(dep_sesion)]


def _a_login(peticion: Request) -> Response:
    if peticion.headers.get("hx-request") == "true":
        return Response(status_code=status.HTTP_401_UNAUTHORIZED, headers={"HX-Redirect": "/login"})
    destino = "/login"
    if peticion.method == "GET" and peticion.url.path not in ("/", "/login"):
        consulta = f"?{peticion.url.query}" if peticion.url.query else ""
        destino = f"/login?siguiente={quote(peticion.url.path + consulta, safe='/')}"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


async def manejar_sin_sesion(peticion: Request, fallo: SesionRequerida) -> Response:
    if not es_superficie_api(peticion.url.path):
        return _a_login(peticion)
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=cuerpo(CodigoError.NO_AUTENTICADO, "Hace falta iniciar sesion"),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def manejar_prohibido(peticion: Request, fallo: AccesoProhibido) -> Response:
    mensaje = "Tu rol no permite esta operación"
    if not es_superficie_api(peticion.url.path):
        if peticion.headers.get("hx-request") == "true":
            return HTMLResponse(
                f'<div class="mensaje mensaje-error" role="alert">{mensaje}</div>',
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if getattr(peticion.app.state, "interfaz", "htmx") == "spa":
            # La SPA dibuja su pantalla de acceso denegado; el 403 queda en la respuesta.
            from app.api.rutas.spa import respuesta_spa

            return respuesta_spa(status.HTTP_403_FORBIDDEN)
        from app.api.rutas.paginas import pagina_prohibida

        return pagina_prohibida(mensaje)
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content=cuerpo(CodigoError.PROHIBIDO, mensaje),
    )


def respuesta_pagina_desconocida(peticion: Request) -> Response | None:
    """Una direccion de pagina que ninguna ruta reconoce, con la SPA como interfaz.

    Con sesion: el `index.html` de la SPA con estado 404, y React dibuja "Pagina no
    encontrada" dentro del marco normal (antes salia el JSON del error, sin portal). Sin
    sesion: al login, igual que cualquier pagina. `None` si no aplica: no es un GET, es
    la API o un estatico, o la interfaz no es la SPA; ahi el 404 JSON de siempre.
    """
    ruta = peticion.url.path
    if (
        peticion.method != "GET"
        or es_superficie_api(ruta)
        or ruta.startswith("/estaticos")
        or getattr(peticion.app.state, "interfaz", "htmx") != "spa"
        or peticion.scope.get("endpoint") is not None  # una ruta existe: el 404 es suyo
    ):
        return None
    servicio = dep_servicio_sesion(peticion)
    if servicio.leer(token_de(peticion, servicio)) is None:
        return _a_login(peticion)
    from app.api.rutas.spa import respuesta_spa

    return respuesta_spa(status.HTTP_404_NOT_FOUND)


def registrar_manejadores_sesion(aplicacion: FastAPI) -> None:
    aplicacion.add_exception_handler(SesionRequerida, manejar_sin_sesion)
    aplicacion.add_exception_handler(AccesoProhibido, manejar_prohibido)

