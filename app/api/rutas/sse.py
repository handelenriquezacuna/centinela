"""El canal SSE de alertas: `GET /api/v1/alertas/flujo`.

Es una ruta delgada a proposito. Todo lo que decide el contrato del canal esta en
`app/api/canal.py` (nombres de evento, token como `id`, resincronizacion) y lo que
consulta la base en `app/repos/flujo_alertas.py`. Aqui solo se envuelve el generador en
una respuesta de tipo `text/event-stream`.

Por que SSE y no WebSocket: el flujo es de una sola direccion -el servidor empuja
alertas, el navegador no manda nada por ese canal- y SSE trae la reconexion y el
`Last-Event-ID` de serie en el navegador. Con WebSocket habria que escribir a mano la
reconexion, el reintento progresivo y la reanudacion, para una conexion que nunca
necesita hablar de vuelta.

Como se conecta el panel (H-17), para que quede escrito antes de que exista:

    <div hx-ext="sse" sse-connect="/api/v1/alertas/flujo">
      <div id="cola" sse-swap="alerta" hx-swap="afterbegin"></div>
      <div hx-get="/api/v1/alertas" hx-trigger="sse:resincronizar" hx-target="#cola"></div>
    </div>

La extension `sse` de HTMX **no viene en el nucleo de htmx**: es un archivo aparte, y
esta vendorizado en `app/estaticos/htmx-ext-sse.js`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, status
from sse_starlette import EventSourceResponse

from app.api.canal import eventos_de_alertas
from app.api.dependencias import AjustesDep, FlujoAlertasDep
from app.api.errores import respuestas_documentadas

enrutador = APIRouter(tags=["alertas"])


@enrutador.get(
    "/alertas/flujo",
    summary="Canal SSE de alertas, leyendo el change stream de la coleccion",
    response_class=EventSourceResponse,
    responses={
        status.HTTP_200_OK: {
            "description": (
                "Flujo `text/event-stream`. Eventos: `alerta` (fragmento HTML de la "
                "fila, con el token de reanudacion como `id`) y `resincronizar` "
                "(el token ya no sirve: pedir la cola a GET /api/v1/alertas)."
            ),
            "content": {"text/event-stream": {}},
        },
        # Los errores se declaran igual que en las demas rutas, y no por gusto: esta
        # ruta tiene parametros, asi que FastAPI le documentaria por omision un 422 con
        # `HTTPValidationError`, que NO es lo que devuelve en ejecucion. Hay una prueba
        # que falla si ese esquema vuelve a aparecer en el documento publicado.
        **respuestas_documentadas(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ),
    },
)
async def flujo_de_alertas(
    ajustes: AjustesDep,
    flujo: FlujoAlertasDep,
    last_event_id: Annotated[
        str | None,
        Header(
            alias="Last-Event-ID",
            description="Token de reanudacion del ultimo evento que el cliente recibio",
        ),
    ] = None,
    ultimo_evento: Annotated[
        str | None,
        Query(
            description=(
                "Mismo valor que la cabecera Last-Event-ID. Existe para poder "
                "reanudar a mano con curl; el navegador usa la cabecera."
            )
        ),
    ] = None,
) -> EventSourceResponse:
    """Abre el canal y lo mantiene abierto hasta que el cliente se va.

    Sobre la reanudacion, que es la parte que hay que entender bien: el navegador
    guarda el `id` del ultimo evento que recibio y lo devuelve en `Last-Event-ID` al
    reconectar. El canal reabre el change stream con `resumeAfter` y ese token, con el
    mismo pipeline y las mismas opciones. Lo que se promete no es reproducir cada
    evento historico: es reconstruir el estado de la cola. Si el token ya no sirve
    llega `resincronizar` y el panel vuelve a pedir su cola completa.

    El `ping` de la respuesta manda un comentario cada `canal.ping_segundos`, para que
    un proxy no cierre una conexion sin trafico cuando no hay alertas.
    """
    return EventSourceResponse(
        eventos_de_alertas(
            flujo,
            id_ultimo_evento=last_event_id or ultimo_evento,
            reintento_ms=ajustes.canal.reintento_ms,
        ),
        ping=ajustes.canal.ping_segundos,
    )
