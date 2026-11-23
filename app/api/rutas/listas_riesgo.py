"""Listas de riesgo: aprobar una cuenta propuesta por la red de mulas y darla de baja (T-51).

- `POST /red-mulas/{cuenta}/propuestas/{propuesta}`: agrega `propuesta` (un SINPE) a
  `listas_riesgo` como vigente, con el motivo "red de mulas desde <cuenta>" y la fecha de
  ahora. Idempotente: si ya hay una entrada vigente de esa cuenta responde 200 con la que
  existe y no escribe; si la crea responde 201. Quien aprobo es la sesion.
- `DELETE /listas-riesgo/{riesgo_id}`: baja real (D-41), 204. 404 si no existe.

Las dos son solo para supervisor y administrador (tabla de `app/seguridad/permisos.py`).
Las pantallas del portal llaman a las mismas funciones (`aprobar_propuesta`, `dar_de_baja`).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Response, status

from app.api.dependencias import BaseDep
from app.api.errores import CodigoError, ErrorCentinela, respuestas_documentadas
from app.api.rutas.inteligencia import CuentaRuta
from app.api.sesion import SesionDep
from app.modelos import EntradaListaRiesgo, ModeloCentinela
from app.repos.listas_riesgo import EntradaNoEncontrada, RepoListasRiesgo
from app.seguridad.tokens import Sesion

enrutador = APIRouter(tags=["listas de riesgo"])


def dep_repo_listas_riesgo(base: BaseDep) -> RepoListasRiesgo:
    return RepoListasRiesgo(base)


RepoListasRiesgoDep = Annotated[RepoListasRiesgo, Depends(dep_repo_listas_riesgo)]

CuentaSinpeRuta = Annotated[
    str, Path(pattern=r"^\d{4}-\d{4}$", description="Numero SINPE de la cuenta, 6033-9001")
]
RiesgoIdRuta = Annotated[
    str, Path(pattern=r"^RSK-\d{3,}$", description="Identificador de la entrada, RSK-001")
]


class ResultadoAprobacion(ModeloCentinela):
    """La entrada vigente y si esta llamada la creo o ya estaba."""

    creada: bool
    entrada: EntradaListaRiesgo


async def aprobar_propuesta(
    repo: RepoListasRiesgo, cuenta_partida: str, propuesta: str, sesion: Sesion
) -> ResultadoAprobacion:
    resultado = await repo.aprobar(
        propuesta, cuenta_partida=cuenta_partida, agente_id=sesion.agente_id
    )
    return ResultadoAprobacion(
        creada=resultado.creada, entrada=EntradaListaRiesgo.model_validate(resultado.entrada)
    )


async def dar_de_baja(repo: RepoListasRiesgo, riesgo_id: str, sesion: Sesion) -> dict[str, Any]:
    try:
        return await repo.dar_de_baja(riesgo_id, agente_id=sesion.agente_id)
    except EntradaNoEncontrada as fallo:
        raise ErrorCentinela(
            CodigoError.NO_ENCONTRADO, str(fallo), status.HTTP_404_NOT_FOUND
        ) from fallo


@enrutador.post(
    "/red-mulas/{cuenta}/propuestas/{propuesta}",
    response_model=ResultadoAprobacion,
    status_code=status.HTTP_201_CREATED,
    summary="Aprueba una cuenta propuesta por la red y la agrega a listas_riesgo",
    responses={
        **respuestas_documentadas(
            status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_500_INTERNAL_SERVER_ERROR
        ),
        status.HTTP_200_OK: {"description": "Ya estaba vigente: no se escribio nada"},
    },
)
async def aprobar(
    cuenta: CuentaRuta,
    propuesta: CuentaSinpeRuta,
    repo: RepoListasRiesgoDep,
    sesion: SesionDep,
    respuesta: Response,
) -> ResultadoAprobacion:
    resultado = await aprobar_propuesta(repo, cuenta, propuesta, sesion)
    if not resultado.creada:
        respuesta.status_code = status.HTTP_200_OK
    return resultado


@enrutador.delete(
    "/listas-riesgo/{riesgo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Da de baja una entrada de listas_riesgo (borrado real)",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def baja(riesgo_id: RiesgoIdRuta, repo: RepoListasRiesgoDep, sesion: SesionDep) -> Response:
    """Caso de negocio: una cuenta reportada por error. Antes de borrar deja el documento
    completo y quien lo borro en el log `centinela.auditoria`."""
    await dar_de_baja(repo, riesgo_id, sesion)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
