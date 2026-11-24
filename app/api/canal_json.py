"""El canal de alertas en JSON para la SPA (T-61): `GET /api/v1/alertas/eventos`.

Es el MISMO canal que `app/api/canal.py` -mismo change stream, mismos nombres de evento,
mismo `id` (token de reanudacion), misma reanudacion por `Last-Event-ID` y el mismo
`resincronizar`-; lo unico que cambia es el cuerpo: JSON en vez de un fragmento HTML.
El bucle es `eventos_de_alertas`, asi que los dos canales no pueden separarse en lo que
importa. Contrato compartido; lo cambia el arquitecto, con su prueba.

Formato (version 1), una linea de JSON por `data`:

    event: alerta
    id: <token de reanudacion, base64url>
    data: {"formato": 1, "operacion": "insert", "alerta": { ...Alerta... }}

    event: resincronizar
    id:
    data: {"formato": 1, "motivo": "el token de reanudacion ya no sirve (...)"}

- `alerta` es la alerta completa con la misma forma que cada elemento de
  `GET /api/v1/alertas` (`_id`, `esquema_version`, `reglas_disparadas`, ...). La SPA la
  reemplaza por `_id`: `insert` es nueva; `update` y `replace` son la misma alerta que
  cambio (una transicion), y puede haber salido de la cola si ya no esta abierta.
- `resincronizar`: el token ya no sirve; la SPA vuelve a pedir su lista completa.
  `id` vacio para que el navegador no reenvie el token muerto.
- `formato` sube si el cuerpo cambia de forma incompatible.

El JSON se escribe aqui con `json.dumps` sobre `ServerSentEvent(data=...)`, que lo manda
tal cual. No se usa `JSONServerSentEvent`: la regla de `canal.py` sigue valiendo, y asi
hay un solo serializador (el de Pydantic, en modo JSON, el mismo de la respuesta HTTP).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from pydantic import ValidationError
from sse_starlette import ServerSentEvent

from app.api.canal import (
    EVENTO_ALERTA,
    EVENTO_RESINCRONIZAR,
    eventos_de_alertas,
    id_desde_token,
)
from app.modelos import Alerta
from app.repos.flujo_alertas import CambioAlerta, ProtocoloFlujoAlertas

registro_log = logging.getLogger("centinela.canal_json")

FORMATO_EVENTO = 1


def _linea(cuerpo: dict[str, Any]) -> str:
    # `ensure_ascii=False`: los nombres con tilde viajan legibles; SSE es UTF-8.
    return json.dumps(cuerpo, ensure_ascii=False, separators=(",", ":"))


def cuerpo_alerta(cambio: CambioAlerta) -> dict[str, Any] | None:
    """El cuerpo JSON de un cambio, o `None` si el documento no es una alerta valida."""
    try:
        alerta = Alerta.model_validate(cambio.documento or {})
    except ValidationError as fallo:
        registro_log.warning(
            "alerta omitida del canal JSON: no cumple el modelo (%s errores)",
            fallo.error_count(),
        )
        return None
    return {
        "formato": FORMATO_EVENTO,
        "operacion": cambio.operacion,
        "alerta": alerta.model_dump(mode="json", by_alias=True),
    }


def evento_alerta_json(cambio: CambioAlerta) -> ServerSentEvent | None:
    cuerpo = cuerpo_alerta(cambio)
    if cuerpo is None:
        return None
    return ServerSentEvent(
        data=_linea(cuerpo), event=EVENTO_ALERTA, id=id_desde_token(cambio.token)
    )


def evento_resincronizar_json(motivo: str) -> ServerSentEvent:
    """Igual que el de HTML: `id=""` para que el navegador olvide el token muerto."""
    return ServerSentEvent(
        data=_linea({"formato": FORMATO_EVENTO, "motivo": motivo}),
        event=EVENTO_RESINCRONIZAR,
        id="",
    )


def eventos_json(
    repo: ProtocoloFlujoAlertas, *, id_ultimo_evento: str | None, reintento_ms: int
) -> AsyncIterator[ServerSentEvent]:
    return eventos_de_alertas(
        repo,
        id_ultimo_evento=id_ultimo_evento,
        reintento_ms=reintento_ms,
        construir_alerta=evento_alerta_json,
        construir_resincronizar=evento_resincronizar_json,
    )
