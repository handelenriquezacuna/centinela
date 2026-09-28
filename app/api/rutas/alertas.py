"""Consulta de alertas: la cola de trabajo del agente.

En H-00A los datos que devuelve son los del conjunto de demo que carga
`python tareas.py datos-demo`. El contrato (filtros, paginacion, forma del error,
forma de la respuesta) ya es el definitivo: cuando el motor de H-09 empiece a escribir
alertas de verdad, esta ruta no cambia.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.dependencias import AjustesDep, RepoAlertasDep
from app.api.errores import respuestas_documentadas
from app.api.esquemas import PaginaAlertas
from app.api.paginacion import acotar_limite
from app.modelos import Alerta, EstadoAlerta, Severidad
from app.modelos.base import asumir_utc

enrutador = APIRouter(tags=["alertas"])


@enrutador.get(
    "/alertas",
    response_model=PaginaAlertas,
    summary="Lista alertas con filtros combinables y paginacion",
    responses=respuestas_documentadas(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def listar_alertas(
    ajustes: AjustesDep,
    repo: RepoAlertasDep,
    estado: Annotated[
        EstadoAlerta | None, Query(description="Estado exacto de la alerta")
    ] = None,
    severidad: Annotated[
        Severidad | None, Query(description="Severidad exacta de la alerta")
    ] = None,
    desde: Annotated[
        datetime | None,
        Query(description="Limite inferior de fecha_creacion, inclusive, con zona"),
    ] = None,
    hasta: Annotated[
        datetime | None,
        Query(description="Limite superior de fecha_creacion, inclusive, con zona"),
    ] = None,
    limite: Annotated[
        int | None,
        Query(ge=1, description="Documentos por pagina; se acota a app.pagina_maxima"),
    ] = None,
    desplazamiento: Annotated[
        int, Query(ge=0, description="Documentos a omitir desde el inicio")
    ] = 0,
) -> PaginaAlertas:
    """Filtros combinables, total del filtro completo y limite acotado por config.

    El orden es por `fecha_creacion` descendente con el identificador como desempate,
    para que paginar no repita ni se salte documentos.
    """
    # Una fecha sin zona en la URL se interpreta como UTC, igual que hace pymongo
    # con un datetime ingenuo. Asi el filtro se comporta igual contra Mongo y contra
    # el doble de prueba, en vez de fallar solo en uno de los dos.
    desde = asumir_utc(desde) if desde is not None else None
    hasta = asumir_utc(hasta) if hasta is not None else None

    limite_efectivo = acotar_limite(
        limite,
        maximo=ajustes.app.pagina_maxima,
        por_omision=ajustes.app.pagina_por_omision,
    )

    documentos, total = await repo.listar(
        estado=estado,
        severidad=severidad,
        desde=desde,
        hasta=hasta,
        limite=limite_efectivo,
        desplazamiento=desplazamiento,
    )

    return PaginaAlertas(
        total=total,
        limite=limite_efectivo,
        desplazamiento=desplazamiento,
        elementos=[Alerta.model_validate(documento) for documento in documentos],
    )
