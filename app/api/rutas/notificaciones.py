"""H-11 · Las notificaciones de D2, para el supervisor: `GET /api/v1/notificaciones`.

Solo lectura. Lo que D2 escribe (una notificacion por alerta CRITICA, con el resultado del
envio) se lista de la mas reciente a la mas antigua, con los datos de la alerta que la
origino. El permiso es `ver_notificaciones`: supervisor y administrador.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.dependencias import AjustesDep, RepoNotificacionesDep
from app.api.errores import respuestas_documentadas
from app.api.esquemas import ElementoNotificacion, PaginaNotificaciones
from app.api.paginacion import acotar_limite
from app.modelos.cierre import EstadoEnvio

enrutador = APIRouter(tags=["notificaciones"])


def elemento_de(documento: dict) -> ElementoNotificacion:
    """Une la notificacion con los datos de su alerta (nulos si la alerta ya no existe)."""
    alerta = (documento.get("alerta") or [{}])[0]
    return ElementoNotificacion(
        notificacion_id=documento["_id"],
        alerta_id=documento["alerta_id"],
        canal=documento["canal"],
        estado_envio=documento["estado_envio"],
        fecha=documento["fecha"],
        intentos=documento.get("intentos"),
        asunto=documento.get("asunto"),
        destinatarios=documento.get("destinatarios"),
        detalle_error=documento.get("detalle_error"),
        severidad=alerta.get("severidad"),
        puntaje=alerta.get("puntaje"),
        monto_crc=int(alerta["monto_crc"]) if alerta.get("monto_crc") is not None else None,
        cliente_nombre=alerta.get("cliente_nombre"),
    )


@enrutador.get(
    "/notificaciones",
    response_model=PaginaNotificaciones,
    summary="Notificaciones de alertas criticas (D2), las mas recientes primero",
    responses=respuestas_documentadas(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def listar_notificaciones(
    ajustes: AjustesDep,
    repo: RepoNotificacionesDep,
    estado_envio: Annotated[
        EstadoEnvio | None, Query(description="Filtra por el resultado del envio")
    ] = None,
    limite: Annotated[
        int | None,
        Query(ge=1, description="Elementos por pagina; se acota a app.pagina_maxima"),
    ] = None,
    desplazamiento: Annotated[
        int, Query(ge=0, description="Elementos a omitir desde el inicio")
    ] = 0,
) -> PaginaNotificaciones:
    """Orden por fecha del ultimo intento, descendente, con el identificador de desempate."""
    aplicado = acotar_limite(
        limite,
        maximo=ajustes.app.pagina_maxima,
        por_omision=ajustes.app.pagina_por_omision,
    )
    documentos, total = await repo.listar(
        estado_envio=estado_envio.value if estado_envio is not None else None,
        limite=aplicado,
        desplazamiento=desplazamiento,
    )
    return PaginaNotificaciones(
        total=total,
        limite=aplicado,
        desplazamiento=desplazamiento,
        elementos=[elemento_de(documento) for documento in documentos],
    )
