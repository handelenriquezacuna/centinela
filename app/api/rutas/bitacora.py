"""Bitacora de auditoria (T-84, D-83): `GET /api/v1/bitacora` y su CSV. Solo administrador.

Une en una lista, de la mas reciente a la mas antigua, lo que cada coleccion ya registra
(transiciones de alertas, ediciones de reglas y politica, altas de listas de riesgo, casos,
notificaciones y respaldos). No hay coleccion de bitacora: es un aggregation pipeline con
`$unionWith` (`app/repos/bitacora.py`) y los manifiestos de respaldos, que viven en disco.

Filtros combinables (AND): `desde`/`hasta`, `actor` (agente), `accion` (repetible) y
`objeto` (prefijo: `ALR-`, `REG-001`, `6033-9001`). Paginacion acotada: el desplazamiento
maximo es `DESPLAZAMIENTO_MAXIMO` y el limite se acota a `app.pagina_maxima`.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from fastapi.responses import Response

from app.api.dependencias import AjustesDep, RepoBitacoraDep, ServicioRespaldosDep
from app.api.errores import (
    CodigoError,
    DetalleError,
    ErrorCentinela,
    respuestas_documentadas,
)
from app.api.esquemas import ElementoBitacora, PaginaBitacora
from app.api.paginacion import acotar_limite
from app.modelos import ZONA_COSTA_RICA
from app.modelos.base import asumir_utc
from app.repos.bitacora import (
    FiltroBitacora,
    coincide,
    eventos_de_manifiesto,
    fusionar,
)

enrutador = APIRouter(tags=["bitacora"])

DESPLAZAMIENTO_MAXIMO = 10_000
LIMITE_EXPORTACION = 10_000
COLUMNAS_CSV = ("fecha_hora_cr", "actor", "accion", "objeto", "detalle", "origen")

ActorQuery = Annotated[str | None, Query(max_length=32, description="Agente, AGT-001")]
AccionQuery = Annotated[list[str] | None, Query(description="Tipo de accion; se repite para varios")]
ObjetoQuery = Annotated[
    str | None, Query(max_length=32, description="Prefijo del objeto: ALR-, REG-001, 6033-9001")
]
DesdeQuery = Annotated[datetime | None, Query(description="Limite inferior de fecha, con zona")]
HastaQuery = Annotated[datetime | None, Query(description="Limite superior de fecha, con zona")]


def filtro_de(
    desde: datetime | None,
    hasta: datetime | None,
    actor: str | None,
    accion: list[str] | None,
    objeto: str | None,
) -> FiltroBitacora:
    filtro = FiltroBitacora(
        desde=asumir_utc(desde) if desde is not None else None,
        hasta=asumir_utc(hasta) if hasta is not None else None,
        actor=actor.strip() if actor and actor.strip() else None,
        acciones=tuple(a.strip() for a in accion or [] if a.strip()),
        objeto=objeto.strip() if objeto and objeto.strip() else None,
    )
    problemas = filtro.problemas()
    if problemas:
        raise ErrorCentinela(
            CodigoError.PARAMETROS_INVALIDOS,
            "La bitacora tiene parametros invalidos",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [DetalleError(campo=campo, mensaje=mensaje) for campo, mensaje in problemas],
        )
    return filtro


async def reunir(
    filtro: FiltroBitacora,
    repo: RepoBitacoraDep,
    respaldos: ServicioRespaldosDep,
    *,
    limite: int,
    desplazamiento: int,
) -> tuple[list[dict], int]:
    """Mongo (top `desplazamiento + limite`) mas los respaldos en disco, ya mezclados."""
    de_mongo, total = await repo.consultar(filtro, desplazamiento + limite)
    de_disco = [
        evento
        for manifiesto in await respaldos.listar()
        for evento in eventos_de_manifiesto(manifiesto)
        if coincide(evento, filtro)
    ]
    mezcladas = fusionar(de_mongo, de_disco)
    return mezcladas[desplazamiento : desplazamiento + limite], total + len(de_disco)


@enrutador.get(
    "/bitacora",
    response_model=PaginaBitacora,
    summary="Bitacora de auditoria: quien hizo que y cuando, las mas recientes primero",
    responses=respuestas_documentadas(
        status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_500_INTERNAL_SERVER_ERROR
    ),
)
async def listar_bitacora(
    ajustes: AjustesDep,
    repo: RepoBitacoraDep,
    respaldos: ServicioRespaldosDep,
    desde: DesdeQuery = None,
    hasta: HastaQuery = None,
    actor: ActorQuery = None,
    accion: AccionQuery = None,
    objeto: ObjetoQuery = None,
    limite: Annotated[
        int | None, Query(ge=1, description="Elementos por pagina; se acota a app.pagina_maxima")
    ] = None,
    desplazamiento: Annotated[
        int,
        Query(ge=0, le=DESPLAZAMIENTO_MAXIMO, description="Elementos a omitir desde el inicio"),
    ] = 0,
) -> PaginaBitacora:
    """Una consulta de administracion, no de la cola: recorre varias colecciones."""
    filtro = filtro_de(desde, hasta, actor, accion, objeto)
    aplicado = acotar_limite(
        limite, maximo=ajustes.app.pagina_maxima, por_omision=ajustes.app.pagina_por_omision
    )
    filas, total = await reunir(
        filtro, repo, respaldos, limite=aplicado, desplazamiento=desplazamiento
    )
    return PaginaBitacora(
        total=total,
        limite=aplicado,
        desplazamiento=desplazamiento,
        elementos=[ElementoBitacora.model_validate(f) for f in filas],
    )


@enrutador.get(
    "/bitacora/exportar.csv",
    response_class=Response,
    summary="Exporta a CSV la bitacora filtrada",
    responses={
        status.HTTP_200_OK: {"content": {"text/csv": {}}, "description": "CSV con encabezado"},
        **respuestas_documentadas(
            status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_500_INTERNAL_SERVER_ERROR
        ),
    },
)
async def exportar_bitacora_csv(
    repo: RepoBitacoraDep,
    respaldos: ServicioRespaldosDep,
    desde: DesdeQuery = None,
    hasta: HastaQuery = None,
    actor: ActorQuery = None,
    accion: AccionQuery = None,
    objeto: ObjetoQuery = None,
) -> Response:
    """Los mismos filtros, hasta `LIMITE_EXPORTACION` filas, con la fecha en hora de Costa Rica.

    El total de coincidencias viaja en `X-Total-Coincidencias` y las filas escritas en
    `X-Filas-Exportadas`.
    """
    filtro = filtro_de(desde, hasta, actor, accion, objeto)
    filas, total = await reunir(
        filtro, repo, respaldos, limite=LIMITE_EXPORTACION, desplazamiento=0
    )
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\r\n")
    escritor.writerow(COLUMNAS_CSV)
    for fila in filas:
        escritor.writerow(
            [
                fila["fecha"].astimezone(ZONA_COSTA_RICA).strftime("%Y-%m-%d %H:%M:%S"),
                fila["actor"] or "",
                fila["accion"],
                fila["objeto"],
                _a_celda(fila["detalle"]),
                fila["origen"],
            ]
        )
    return Response(
        salida.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="bitacora.csv"',
            "X-Total-Coincidencias": str(total),
            "X-Filas-Exportadas": str(len(filas)),
        },
    )


def _a_celda(texto: str) -> str:
    """Neutraliza una celda que una hoja de calculo leeria como formula (=, +, -, @)."""
    return f"'{texto}" if texto[:1] in ("=", "+", "-", "@") else texto
