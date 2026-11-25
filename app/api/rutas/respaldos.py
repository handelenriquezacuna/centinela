"""Respaldos de la base (D-74, Unidad 8.6): listar, crear y verificar. Solo administrador.

- `GET /api/v1/respaldos`: los respaldos del directorio configurado, el mas reciente
  primero, cada uno con su manifiesto y, si se verifico, el resultado.
- `POST /api/v1/respaldos`: corre `mongodump` y deja el archivo y su manifiesto. 201.
- `POST /api/v1/respaldos/{respaldo_id}/verificar`: restaura el archivo en una base
  temporal, compara los conteos con el manifiesto, borra la temporal y guarda el resultado.
  200 aunque la verificacion de `fallida`: el resultado es el dato, no un error del servidor.

Una operacion a la vez: si hay otra en curso, 409 `conflicto`. Todo queda en el log
`centinela.auditoria` con el agente de la sesion.

Restaurar sobre la base viva NO es una ruta. Es `python tareas.py restaurar` (con respaldo
previo automatico), y el portal solo muestra el comando.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status
from pydantic import Field

from app.api.dependencias import ServicioRespaldosDep
from app.api.errores import CodigoError, ErrorCentinela, respuestas_documentadas
from app.api.sesion import SesionDep
from app.modelos import ModeloCentinela
from app.respaldos.manifiesto import PATRON_ID, Manifiesto
from app.respaldos.servicio import (
    HerramientaFallida,
    OperacionEnCurso,
    RespaldoNoEncontrado,
)

enrutador = APIRouter(tags=["respaldos"])

RespaldoIdRuta = Annotated[
    str, Path(pattern=PATRON_ID, description="Identificador del respaldo, RSP-20261124T153012Z")
]


class ListaRespaldos(ModeloCentinela):
    elementos: list[Manifiesto]
    total: int = Field(ge=0)
    # Hay una operacion corriendo ahora: el portal deshabilita Crear y Verificar.
    en_curso: bool


def _error(fallo: Exception) -> ErrorCentinela:
    if isinstance(fallo, OperacionEnCurso):
        return ErrorCentinela(CodigoError.CONFLICTO, str(fallo), status.HTTP_409_CONFLICT)
    if isinstance(fallo, RespaldoNoEncontrado):
        return ErrorCentinela(CodigoError.NO_ENCONTRADO, str(fallo), status.HTTP_404_NOT_FOUND)
    return ErrorCentinela(
        CodigoError.ERROR_INTERNO, str(fallo), status.HTTP_500_INTERNAL_SERVER_ERROR
    )


@enrutador.get(
    "/respaldos",
    response_model=ListaRespaldos,
    summary="Lista los respaldos con su manifiesto y el resultado de su verificacion",
    responses=respuestas_documentadas(status.HTTP_500_INTERNAL_SERVER_ERROR),
)
async def listar(servicio: ServicioRespaldosDep) -> ListaRespaldos:
    elementos = await servicio.listar()
    return ListaRespaldos(elementos=elementos, total=len(elementos), en_curso=servicio.en_curso)


@enrutador.post(
    "/respaldos",
    response_model=Manifiesto,
    status_code=status.HTTP_201_CREATED,
    summary="Crea un respaldo (mongodump) de la base y su manifiesto",
    responses=respuestas_documentadas(
        status.HTTP_409_CONFLICT, status.HTTP_500_INTERNAL_SERVER_ERROR
    ),
)
async def crear(servicio: ServicioRespaldosDep, sesion: SesionDep) -> Manifiesto:
    try:
        return await servicio.crear(sesion.agente_id)
    except (OperacionEnCurso, HerramientaFallida) as fallo:
        raise _error(fallo) from fallo


@enrutador.post(
    "/respaldos/{respaldo_id}/verificar",
    response_model=Manifiesto,
    summary="Restaura el respaldo en una base temporal, compara conteos y la borra",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def verificar(
    respaldo_id: RespaldoIdRuta, servicio: ServicioRespaldosDep, sesion: SesionDep
) -> Manifiesto:
    try:
        return await servicio.verificar(respaldo_id, sesion.agente_id)
    except (OperacionEnCurso, RespaldoNoEncontrado, HerramientaFallida) as fallo:
        raise _error(fallo) from fallo
