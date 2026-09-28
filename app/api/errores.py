"""Forma estable del error de la API.

Una sola forma para todos los errores, sea un 404, un 422 de validacion o un 500
inesperado:

    {
      "error": {
        "codigo": "parametros_invalidos",
        "mensaje": "La peticion tiene parametros invalidos",
        "detalles": [{"campo": "limite", "mensaje": "debe ser mayor o igual a 1"}]
      }
    }

Por que no se usa el `{"detail": ...}` que trae FastAPI: `detail` cambia de tipo
segun el caso (texto en un `HTTPException`, lista de objetos en un error de
validacion), asi que el cliente tiene que ramificar por tipo para mostrar un mensaje.
El portal necesita un solo camino: leer `error.mensaje` y, si quiere detalle,
recorrer `error.detalles`.

`codigo` es para el programa y es estable; `mensaje` es para la persona y puede
cambiar de redaccion sin romper a nadie.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.modelos.estados import TransicionInvalida


class CodigoError(StrEnum):
    """Vocabulario cerrado de codigos de error. Agregar uno es cambiar el contrato."""

    PARAMETROS_INVALIDOS = "parametros_invalidos"
    NO_ENCONTRADO = "no_encontrado"
    CONFLICTO = "conflicto"
    TRANSICION_INVALIDA = "transicion_invalida"
    BASE_NO_DISPONIBLE = "base_no_disponible"
    ERROR_INTERNO = "error_interno"


class DetalleError(BaseModel):
    """Un problema concreto. `campo` es nulo cuando el error no es de un campo."""

    campo: str | None = Field(default=None, examples=["limite"])
    mensaje: str = Field(examples=["debe ser mayor o igual a 1"])


class CuerpoError(BaseModel):
    codigo: CodigoError = Field(examples=[CodigoError.PARAMETROS_INVALIDOS])
    mensaje: str = Field(examples=["La peticion tiene parametros invalidos"])
    detalles: list[DetalleError] = Field(default_factory=list)


class RespuestaError(BaseModel):
    """Cuerpo de TODA respuesta de error de la API."""

    error: CuerpoError


class ErrorCentinela(Exception):
    """Error de dominio con su codigo y su estado HTTP ya decididos.

    Se lanza desde los servicios y los routers; el manejador de abajo lo convierte en
    la respuesta. Asi el codigo de dominio no construye `JSONResponse`.
    """

    def __init__(
        self,
        codigo: CodigoError,
        mensaje: str,
        estado_http: int = status.HTTP_400_BAD_REQUEST,
        detalles: list[DetalleError] | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.estado_http = estado_http
        self.detalles = detalles or []


# Codigo por omision segun el estado HTTP, para los `HTTPException` que lanza
# FastAPI por su cuenta (una ruta que no existe, un metodo no permitido).
CODIGO_POR_ESTADO: dict[int, CodigoError] = {
    status.HTTP_400_BAD_REQUEST: CodigoError.PARAMETROS_INVALIDOS,
    status.HTTP_404_NOT_FOUND: CodigoError.NO_ENCONTRADO,
    status.HTTP_409_CONFLICT: CodigoError.CONFLICTO,
    status.HTTP_422_UNPROCESSABLE_CONTENT: CodigoError.PARAMETROS_INVALIDOS,
    status.HTTP_503_SERVICE_UNAVAILABLE: CodigoError.BASE_NO_DISPONIBLE,
}


def cuerpo(
    codigo: CodigoError,
    mensaje: str,
    detalles: list[DetalleError] | None = None,
) -> dict[str, Any]:
    """Serializa la respuesta de error. Un solo lugar arma este diccionario."""
    return RespuestaError(
        error=CuerpoError(codigo=codigo, mensaje=mensaje, detalles=detalles or [])
    ).model_dump(mode="json")


def _respuesta(
    estado_http: int,
    codigo: CodigoError,
    mensaje: str,
    detalles: list[DetalleError] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=estado_http, content=cuerpo(codigo, mensaje, detalles)
    )


def _campo_legible(ubicacion: tuple[Any, ...]) -> str | None:
    """Nombre del campo a partir del `loc` de Pydantic.

    El `loc` viene como ("query", "limite") o ("body", "historial", 0, "nota"); al
    portal le sirve "limite" y "historial.0.nota", no la tupla con el origen.
    """
    partes = [str(parte) for parte in ubicacion[1:]]
    return ".".join(partes) if partes else None


async def manejar_validacion(
    peticion: Request, fallo: RequestValidationError
) -> JSONResponse:
    """422 de validacion, traducido a la forma unica."""
    detalles = [
        DetalleError(campo=_campo_legible(error["loc"]), mensaje=error["msg"])
        for error in fallo.errors()
    ]
    return _respuesta(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        CodigoError.PARAMETROS_INVALIDOS,
        "La peticion tiene parametros invalidos",
        detalles,
    )


async def manejar_error_centinela(
    peticion: Request, fallo: ErrorCentinela
) -> JSONResponse:
    return _respuesta(fallo.estado_http, fallo.codigo, fallo.mensaje, fallo.detalles)


async def manejar_transicion_invalida(
    peticion: Request, fallo: TransicionInvalida
) -> JSONResponse:
    """La maquina de estados de la alerta, expuesta como 409.

    Es 409 y no 422: el cuerpo de la peticion es valido, lo que no se puede es
    aplicarlo al estado actual del recurso. Lo usa H-14 al resolver una alerta.
    """
    return _respuesta(
        status.HTTP_409_CONFLICT,
        CodigoError.TRANSICION_INVALIDA,
        str(fallo),
        [DetalleError(campo="estado", mensaje=f"estado actual: {fallo.origen}")],
    )


async def manejar_http(
    peticion: Request, fallo: StarletteHTTPException
) -> JSONResponse:
    codigo = CODIGO_POR_ESTADO.get(fallo.status_code, CodigoError.ERROR_INTERNO)
    return _respuesta(fallo.status_code, codigo, str(fallo.detail))


async def manejar_inesperado(peticion: Request, fallo: Exception) -> JSONResponse:
    """500. No se filtra el detalle interno al cliente, va al registro.

    Sin este manejador, una excepcion no prevista sale con el HTML de Starlette y
    rompe el contrato justo cuando el portal mas necesita poder leer el error.
    """
    return _respuesta(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        CodigoError.ERROR_INTERNO,
        "Error interno del servidor",
    )


def registrar_manejadores(aplicacion: FastAPI) -> None:
    """Conecta los manejadores. Se llama una vez, al construir la aplicacion."""
    aplicacion.add_exception_handler(RequestValidationError, manejar_validacion)
    aplicacion.add_exception_handler(ErrorCentinela, manejar_error_centinela)
    aplicacion.add_exception_handler(TransicionInvalida, manejar_transicion_invalida)
    aplicacion.add_exception_handler(StarletteHTTPException, manejar_http)
    aplicacion.add_exception_handler(Exception, manejar_inesperado)


def respuestas_documentadas(*estados: int) -> dict[int | str, dict[str, Any]]:
    """Bloque `responses` para que OpenAPI publique la forma del error.

    Sin esto el esquema publicado miente: dice que un 422 devuelve
    `HTTPValidationError` cuando en tiempo de ejecucion devuelve `RespuestaError`.
    """
    descripciones = {
        status.HTTP_404_NOT_FOUND: "Recurso no encontrado",
        status.HTTP_409_CONFLICT: "Conflicto con el estado actual del recurso",
        status.HTTP_422_UNPROCESSABLE_CONTENT: "Parametros invalidos",
        status.HTTP_500_INTERNAL_SERVER_ERROR: "Error interno del servidor",
        status.HTTP_503_SERVICE_UNAVAILABLE: "La base de datos no responde",
    }
    return {
        estado: {
            "model": RespuestaError,
            "description": descripciones.get(estado, "Error"),
        }
        for estado in estados
    }
