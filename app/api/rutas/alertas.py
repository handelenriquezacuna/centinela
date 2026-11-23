"""Alertas: la cola de trabajo del agente (H-13) y resolverlas (H-14).

En H-00A los datos que devuelve son los del conjunto de demo que carga
`python tareas.py datos-demo`. El contrato (filtros, paginacion, forma del error,
forma de la respuesta) ya es el definitivo: cuando el motor de H-09 empiece a escribir
alertas de verdad, esta ruta no cambia.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.dependencias import AjustesDep, RepoAlertasDep
from app.api.errores import (
    CodigoError,
    DetalleError,
    ErrorCentinela,
    respuestas_documentadas,
)
from app.api.esquemas import PaginaAlertas, PeticionTransicion, ResultadoTransicion
from app.api.paginacion import acotar_limite
from app.api.sesion import SesionDep
from app.modelos import Alerta, EstadoAlerta, Severidad
from app.modelos.base import asumir_utc
from app.repos.alertas import ConflictoAlerta, planear_transicion

enrutador = APIRouter(tags=["alertas"])

AlertaIdRuta = Annotated[
    str, Path(pattern=r"^ALR-\d{3,}$", description="Identificador de la alerta, ALR-001")
]


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


def _conflicto(fallo: ConflictoAlerta) -> ErrorCentinela:
    detalles = (
        [DetalleError(campo="estado", mensaje=f"estado actual: {fallo.estado_actual}")]
        if fallo.estado_actual is not None
        else []
    )
    return ErrorCentinela(
        CodigoError.CONFLICTO, str(fallo), status.HTTP_409_CONFLICT, detalles
    )


@enrutador.post(
    "/alertas/{alerta_id}/transiciones",
    response_model=ResultadoTransicion,
    summary="Mueve la alerta por la maquina de estados y lo agrega al historial",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def transicionar_alerta(
    alerta_id: AlertaIdRuta,
    peticion: PeticionTransicion,
    repo: RepoAlertasDep,
    sesion: SesionDep,
) -> ResultadoTransicion:
    """Una transicion es un recurso que se agrega, no un estado que se pisa: POST.

    - Valida contra `app/modelos/estados.py`. Fuera de la tabla: 409
      `transicion_invalida`, con el estado actual en `detalles`.
    - Agrega al historial con `$push` y condiciona la escritura al estado y al agente
      leidos. Si otro agente la tomo o la resolvio en el medio: 409 `conflicto`.
    - `en_revision -> X` solo la hace el agente que la tomo; otro recibe 409.
    - `escalada` crea el caso `CAS-xxx` en la misma transaccion multi-documento.

    El agente de la accion es el de la sesion (`agente_id` del cuerpo se ignora): el
    historial no se puede firmar a nombre de otro.
    """
    actual = await repo.obtener(alerta_id)
    if actual is None:
        raise ErrorCentinela(
            CodigoError.NO_ENCONTRADO,
            f"No existe la alerta {alerta_id}",
            status.HTTP_404_NOT_FOUND,
        )
    if peticion.estado_esperado is not None and peticion.estado_esperado != actual["estado"]:
        raise _conflicto(
            ConflictoAlerta(
                f"La alerta {alerta_id} ya no esta en {peticion.estado_esperado}",
                estado_actual=actual["estado"],
            )
        )

    try:
        # TransicionInvalida sale tal cual: su manejador la publica como 409.
        plan = planear_transicion(
            actual,
            destino=peticion.destino,
            agente_id=sesion.agente_id,
            nota=peticion.nota,
            ahora=datetime.now(UTC),
        )
        actualizada = await repo.aplicar_transicion(plan)
    except ConflictoAlerta as fallo:
        raise _conflicto(fallo) from fallo

    return ResultadoTransicion(
        alerta=Alerta.model_validate(actualizada),
        caso_id=plan.caso["_id"] if plan.caso is not None else None,
    )
