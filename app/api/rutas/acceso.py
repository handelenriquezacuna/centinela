"""Entrar y salir (H-25): pantalla `/login`, `/salir`, el token de la API y el OpenAPI.

- `GET/POST /login`: formulario del portal. Si las credenciales sirven, deja la cookie
  de sesion (HttpOnly, SameSite=Strict, `Secure` segun `sesion.cookie_segura`) y vuelve a
  donde iba el usuario.
- `POST /salir`: borra la cookie.
- `POST /api/v1/sesion`: lo mismo para clientes de la API; devuelve el token para usarlo
  como `Authorization: Bearer`.
- `GET /openapi.json`: el documento de la API, ahora detras de sesion. `/docs` y
  `/redoc` se retiraron: cargaban su JavaScript de una CDN y el portal no depende de una.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencias import BaseDep
from app.api.errores import CodigoError, ErrorCentinela, respuestas_documentadas
from app.api.rutas.paginas import ENTORNO
from app.api.sesion import ServicioSesionDep
from app.modelos.alertas import RolAgente
from app.modelos.base import AgenteId
from app.repos.agentes import ProtocoloRepoAgentes, RepoAgentes
from app.seguridad.autenticacion import autenticar

enrutador = APIRouter(include_in_schema=False)
enrutador_api = APIRouter(tags=["sesion"])

MENSAJE_CREDENCIALES = "Identificador o contrasena incorrectos"
DESTINO_POR_OMISION = "/panel"


def dep_repo_agentes(base: BaseDep) -> ProtocoloRepoAgentes:
    return RepoAgentes(base)


RepoAgentesDep = Annotated[ProtocoloRepoAgentes, Depends(dep_repo_agentes)]


def destino_seguro(siguiente: str | None) -> str:
    """Solo rutas internas: un `siguiente=//otro.sitio` no puede sacar al usuario del portal.

    Los navegadores quitan tabuladores y saltos de linea de una URL, asi que
    `/<TAB>/otro.sitio` termina siendo `//otro.sitio`: se rechaza cualquier caracter de
    control o espacio, y ademas la ruta interpretada no puede traer esquema ni dominio.
    """
    if (
        not siguiente
        or not siguiente.startswith("/")
        or any(ord(c) < 0x21 or ord(c) == 0x7F for c in siguiente)
        or "\\" in siguiente
        or siguiente.startswith("//")
    ):
        return DESTINO_POR_OMISION
    partes = urlsplit(siguiente)
    if partes.scheme or partes.netloc or partes.path in ("/login", "/salir"):
        return DESTINO_POR_OMISION
    return siguiente


def _pagina_login(
    peticion: Request, *, mensaje: str | None = None, agente_id: str = "", estado: int = 200
) -> HTMLResponse:
    html = ENTORNO.get_template("paginas/login.html").render(
        navegacion=[],
        activa="",
        mensaje=mensaje,
        agente_id=agente_id,
        siguiente=destino_seguro(peticion.query_params.get("siguiente")),
    )
    return HTMLResponse(html, status_code=estado)


@enrutador.get("/login", response_class=HTMLResponse)
async def formulario_login(peticion: Request) -> HTMLResponse:
    return _pagina_login(peticion)


@enrutador.post("/login", response_class=HTMLResponse)
async def iniciar_sesion_portal(
    peticion: Request, repo: RepoAgentesDep, servicio: ServicioSesionDep
) -> Response:
    cuerpo = parse_qs((await peticion.body()).decode("utf-8"), keep_blank_values=True)

    def campo(nombre: str) -> str:
        return cuerpo.get(nombre, [""])[0]

    agente_id = campo("agente_id").strip()
    agente = await autenticar(repo, agente_id, campo("contrasena"))
    if agente is None:
        return _pagina_login(
            peticion,
            mensaje=MENSAJE_CREDENCIALES,
            agente_id=agente_id,
            estado=status.HTTP_401_UNAUTHORIZED,
        )
    token, _ = servicio.emitir(agente["_id"], RolAgente(agente["rol"]))
    respuesta = RedirectResponse(
        destino_seguro(campo("siguiente")), status_code=status.HTTP_303_SEE_OTHER
    )
    respuesta.set_cookie(
        servicio.nombre_cookie,
        token,
        max_age=servicio.minutos * 60,
        httponly=True,
        samesite="strict",
        secure=servicio.cookie_segura,
        path="/",
    )
    return respuesta


@enrutador.post("/salir")
async def salir(servicio: ServicioSesionDep) -> RedirectResponse:
    respuesta = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    respuesta.delete_cookie(
        servicio.nombre_cookie,
        path="/",
        httponly=True,
        samesite="strict",
        secure=servicio.cookie_segura,
    )
    return respuesta


@enrutador.get("/openapi.json")
async def openapi(peticion: Request) -> JSONResponse:
    return JSONResponse(peticion.app.openapi())


class SolicitudSesion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agente_id: AgenteId
    contrasena: str = Field(min_length=1, max_length=256)


class RespuestaSesion(BaseModel):
    """El token y de quien es. `expira` en UTC; el token deja de servir a esa hora."""

    token: str
    tipo: str = "Bearer"
    agente_id: str
    rol: RolAgente
    expira: datetime


@enrutador_api.post(
    "/sesion",
    response_model=RespuestaSesion,
    summary="Inicia sesion y devuelve el token para `Authorization: Bearer`",
    responses=respuestas_documentadas(
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    ),
)
async def iniciar_sesion_api(
    solicitud: SolicitudSesion, repo: RepoAgentesDep, servicio: ServicioSesionDep
) -> RespuestaSesion:
    agente: dict[str, Any] | None = await autenticar(
        repo, solicitud.agente_id, solicitud.contrasena
    )
    if agente is None:
        raise ErrorCentinela(
            CodigoError.NO_AUTENTICADO, MENSAJE_CREDENCIALES, status.HTTP_401_UNAUTHORIZED
        )
    rol = RolAgente(agente["rol"])
    token, expira = servicio.emitir(agente["_id"], rol)
    return RespuestaSesion(token=token, agente_id=agente["_id"], rol=rol, expira=expira)
