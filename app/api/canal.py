"""El canal de alertas en vivo: change stream de `alertas` traducido a eventos SSE.

Este modulo es el contrato del tiempo real de Centinela. Cambiarlo es cambiar lo que
el portal (H-17) recibe, asi que lo cambia el arquitecto y con su prueba en el mismo
commit. Lo que fija:

| Pieza | Decision |
|---|---|
| Fuente | El change stream de `alertas`. No hay bus en memoria: la fuente es lo persistido |
| `id` del evento | El token de reanudacion del change stream, codificado en base64url |
| Reanudacion | `resumeAfter` con ese token, con el MISMO pipeline y las MISMAS opciones |
| Token inservible | Evento `resincronizar`, y el panel vuelve a pedir su cola a `GET /api/v1/alertas` |
| Cuerpo | Un fragmento HTML listo para insertar, no JSON |

**La garantia, dicha con precision:** no se reproduce cada evento historico, se
reconstruye el estado de la cola. Si el token sirve, el panel recibe lo que se perdio
mientras estaba desconectado; si no sirve, recibe `resincronizar` y vuelve a pedir la
cola completa. Para un panel de trabajo eso es lo correcto: el agente quiere su cola
como esta ahora, no la repeticion de cada transicion por la que paso.

**Sobre `data` y el HTML, que es un detalle con trampa.** El cuerpo tiene que llegar
verbatim, porque HTMX lo inserta en el DOM: si se serializara como JSON llegaria
entrecomillado y el panel pintaria las comillas. En `sse-starlette` 3.4.11 la clase
`ServerSentEvent` escribe `data` tal cual (`str(self.data)`, sin `json.dumps`), y la
que serializa es su hermana `JSONServerSentEvent`. Entonces la regla operativa aqui es:
**`ServerSentEvent` si, `JSONServerSentEvent` nunca.** Hay dos pruebas que lo sostienen
en vez de confiar en el recuerdo de una version: una lee los bytes del evento y exige
el HTML sin comillas, y otra falla si este modulo importa la variante JSON.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

from sse_starlette import ServerSentEvent

from app.api.fragmentos import fragmento_alerta
from app.repos.flujo_alertas import (
    CambioAlerta,
    ProtocoloFlujoAlertas,
    TokenDeReanudacionInvalido,
)

registro_log = logging.getLogger("centinela.canal")

# Nombres de evento. El panel se suscribe por nombre (`sse-swap="alerta"`), asi que
# renombrarlos rompe el portal en silencio.
EVENTO_ALERTA = "alerta"
EVENTO_RESINCRONIZAR = "resincronizar"


class IdDeEventoInvalido(Exception):
    """El `Last-Event-ID` que trajo el cliente no se puede decodificar.

    Es distinto de `TokenDeReanudacionInvalido`: aca el id no llega a ser un token, se
    rompe antes de tocar Mongo. El efecto para el panel es el mismo, `resincronizar`.
    """


def id_desde_token(token: Mapping[str, Any]) -> str:
    """Token de reanudacion -> `id` del evento SSE.

    Base64url sin relleno sobre el JSON del token. Se codifica por tres razones: el
    `id` de SSE no puede llevar saltos de linea, el token es opaco y no hay que
    invitar a nadie a interpretarlo, y asi cualquier forma futura de token (hoy es
    `{"_data": "<hex>"}`) sigue cabiendo sin cambiar el contrato.

    `default=str` es una red de seguridad, no una conversion esperada: si algun dia un
    token trae un tipo que JSON no sabe escribir, el canal no se cae. En el peor caso
    el token vuelve deformado, Mongo lo rechaza y el panel recibe `resincronizar`, que
    es justo la recuperacion que ya esta construida.
    """
    crudo = json.dumps(token, separators=(",", ":"), sort_keys=True, default=str)
    return base64.urlsafe_b64encode(crudo.encode("utf-8")).decode("ascii").rstrip("=")


def token_desde_id(identificador: str) -> Mapping[str, Any]:
    """`id` del evento SSE -> token de reanudacion. Inversa exacta de la anterior."""
    relleno = "=" * (-len(identificador) % 4)
    try:
        crudo = base64.urlsafe_b64decode(identificador + relleno)
        token = json.loads(crudo)
    except (binascii.Error, UnicodeDecodeError, ValueError) as fallo:
        raise IdDeEventoInvalido(
            f"el Last-Event-ID no es un token de reanudacion ({type(fallo).__name__})"
        ) from fallo

    if not isinstance(token, dict) or not token:
        raise IdDeEventoInvalido("el Last-Event-ID no contiene un token de reanudacion")
    return token


def evento_apertura(*, reintento_ms: int, reanudado: bool) -> ServerSentEvent:
    """Primer evento: un comentario y el `retry` del navegador.

    Sirve para dos cosas concretas. Una, fija cuanto espera el navegador antes de
    reconectar en vez de dejarlo en el valor que cada navegador traiga. Dos, manda
    bytes de inmediato: sin eso, las cabeceras de la respuesta pueden quedarse en un
    buffer intermedio hasta la primera alerta, y el canal parece colgado cuando lo que
    pasa es que no hay alertas todavia.
    """
    return ServerSentEvent(
        comment=f"canal de alertas abierto (reanudado={'si' if reanudado else 'no'})",
        retry=reintento_ms,
    )


def evento_alerta(cambio: CambioAlerta) -> ServerSentEvent:
    """Una alerta, como fragmento HTML, con el token de reanudacion como `id`."""
    return ServerSentEvent(
        data=fragmento_alerta(cambio.documento or {}, operacion=cambio.operacion),
        event=EVENTO_ALERTA,
        id=id_desde_token(cambio.token),
    )


def evento_resincronizar(motivo: str) -> ServerSentEvent:
    """El token ya no sirve: el panel tiene que volver a pedir su cola.

    `id=""` no es un descuido. Segun la especificacion de SSE, un campo `id` vacio
    pone el ultimo identificador del navegador en cadena vacia, asi que la siguiente
    reconexion NO vuelve a mandar el token muerto. Sin esto, el navegador insistiria
    con el mismo `Last-Event-ID` en cada reconexion y pediria resincronizar para
    siempre.
    """
    return ServerSentEvent(data=motivo, event=EVENTO_RESINCRONIZAR, id="")


async def eventos_de_alertas(
    repo: ProtocoloFlujoAlertas,
    *,
    id_ultimo_evento: str | None = None,
    reintento_ms: int,
) -> AsyncIterator[ServerSentEvent]:
    """Genera el flujo completo de una conexion: apertura, alertas y resincronizaciones.

    Esta funcion es el canal entero y no depende de FastAPI: recibe el repositorio que
    observa la coleccion y el identificador del ultimo evento que el cliente vio, y
    devuelve eventos. Por eso se puede probar sin levantar un servidor, que es como se
    prueba el evento `resincronizar` sin tener que romper el oplog de verdad.
    """
    token: Mapping[str, Any] | None = None
    motivo: str | None = None

    if id_ultimo_evento:
        try:
            token = token_desde_id(id_ultimo_evento)
        except IdDeEventoInvalido as fallo:
            motivo = str(fallo)

    yield evento_apertura(reintento_ms=reintento_ms, reanudado=token is not None)

    while True:
        if motivo is not None:
            registro_log.info("canal resincronizando: %s", motivo)
            yield evento_resincronizar(motivo)
            motivo = None
            token = None

        try:
            async for cambio in repo.observar(desde_token=token):
                # El token se guarda para la reapertura: si el change stream se cae y
                # hay que volver a abrirlo, se sigue desde el ultimo evento entregado
                # y no desde el principio.
                token = cambio.token
                yield evento_alerta(cambio)
        except TokenDeReanudacionInvalido as fallo:
            motivo = str(fallo)
            continue

        # El flujo termino sin error: la coleccion se invalido (un drop, un rename).
        # Se cierra la respuesta y el navegador reconecta solo con su ultimo id.
        return
