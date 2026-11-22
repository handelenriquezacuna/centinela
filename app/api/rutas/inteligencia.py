"""Inteligencia: efectividad del motor (H-10) y red de cuentas mula (H-23).

- `GET /efectividad`: el reporte de H-10, el mismo que imprime
  `uv run python -m app.deteccion.efectividad`. Se puede pedir despues de cada ajuste
  de reglas. Lee la etiqueta oculta `simulacion`; el motor no.
- `GET /red-mulas/{cuenta}`: la red que sale de una cuenta (numero SINPE o `CTA-...`),
  como nodos y aristas, y las cuentas propuestas para `listas_riesgo`. Solo propone: no
  escribe en la lista.

Las dependencias de estos repositorios viven aqui y no en `app/api/dependencias.py`:
solo las usan estas dos rutas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.dependencias import BaseDep
from app.api.errores import CodigoError, ErrorCentinela, respuestas_documentadas
from app.deteccion.efectividad import ReporteEfectividad, calcular_efectividad
from app.modelos.base import asumir_utc
from app.repos.efectividad import RepoEfectividad
from app.repos.red_mulas import (
    SALTOS_MAXIMOS,
    VENTANA_MINUTOS,
    CuentaNoEncontrada,
    RedMulas,
    RepoRedMulas,
)

enrutador = APIRouter(tags=["inteligencia"])


def dep_repo_efectividad(base: BaseDep) -> RepoEfectividad:
    return RepoEfectividad(base)


def dep_repo_red_mulas(base: BaseDep) -> RepoRedMulas:
    return RepoRedMulas(base)


RepoEfectividadDep = Annotated[RepoEfectividad, Depends(dep_repo_efectividad)]
RepoRedMulasDep = Annotated[RepoRedMulas, Depends(dep_repo_red_mulas)]

CuentaRuta = Annotated[
    str,
    Path(
        pattern=r"^(CTA-\d{3,}|\d{4}-\d{4})$",
        description="Numero SINPE (6033-9001) o identificador de cuenta (CTA-001)",
    ),
]


@enrutador.get(
    "/efectividad",
    response_model=ReporteEfectividad,
    summary="Efectividad del motor contra la etiqueta oculta del generador",
    responses=respuestas_documentadas(status.HTTP_500_INTERNAL_SERVER_ERROR),
)
async def efectividad(repo: RepoEfectividadDep) -> ReporteEfectividad:
    filas, alertas_totales = await repo.leer_filas_etiquetadas()
    return calcular_efectividad(filas, alertas_totales=alertas_totales)


@enrutador.get(
    "/red-mulas/{cuenta}",
    response_model=RedMulas,
    summary="Sigue el dinero desde una cuenta, hasta 6 saltos",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def red_mulas(
    cuenta: CuentaRuta,
    repo: RepoRedMulasDep,
    desde: Annotated[
        datetime | None,
        Query(
            description="Instante desde el que se siguen las salidas de la cuenta "
            "(la fecha de la transaccion de la alerta). Sin el, todas sus salidas"
        ),
    ] = None,
    ventana_minutos: Annotated[
        int,
        Query(ge=1, le=1440, description="Tiempo maximo entre que llega el dinero y sale"),
    ] = VENTANA_MINUTOS,
    saltos: Annotated[
        int, Query(ge=1, le=SALTOS_MAXIMOS, description="Saltos maximos a recorrer")
    ] = SALTOS_MAXIMOS,
) -> RedMulas:
    try:
        return await repo.recorrer(
            cuenta,
            desde=asumir_utc(desde) if desde is not None else None,
            ventana_minutos=ventana_minutos,
            saltos=saltos,
        )
    except CuentaNoEncontrada as fallo:
        raise ErrorCentinela(
            CodigoError.NO_ENCONTRADO, str(fallo), status.HTTP_404_NOT_FOUND
        ) from fallo
