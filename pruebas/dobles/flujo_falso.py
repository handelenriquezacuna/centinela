"""Doble del observador del change stream de `alertas`.

Permite ejercitar el canal SSE completo -apertura, evento `alerta`, reanudacion con
token y evento `resincronizar`- sin Mongo arriba y sin tener que romper el oplog.

Cumple `ProtocoloFlujoAlertas` de forma estructural: si la firma del repositorio real
cambia, `prueba_el_doble_cumple_el_protocolo` lo dice en vez de dejar que las pruebas
del canal sigan verdes contra una forma que ya no existe.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from app.repos.flujo_alertas import CambioAlerta, TokenDeReanudacionInvalido


def token(numero: int) -> dict[str, str]:
    """Token con la misma forma que el real de MongoDB 8: `{"_data": "<hex>"}`."""
    return {"_data": f"82{numero:06X}"}


class FlujoAlertasFalso:
    """Entrega una lista de cambios y registra con que token se le pidio cada apertura.

    `rechazar_token` imita el caso que en Mongo se llama `ChangeStreamHistoryLost`: la
    primera apertura con token falla, el canal tiene que emitir `resincronizar` y
    volver a abrir sin token.
    """

    def __init__(
        self,
        cambios: Sequence[CambioAlerta],
        *,
        rechazar_token: bool = False,
    ) -> None:
        self._cambios = list(cambios)
        self._rechazar_token = rechazar_token
        self.aperturas: list[Mapping[str, Any] | None] = []

    async def observar(
        self, *, desde_token: Mapping[str, Any] | None = None
    ) -> AsyncIterator[CambioAlerta]:
        self.aperturas.append(desde_token)

        if desde_token is not None and self._rechazar_token:
            raise TokenDeReanudacionInvalido(
                "el token de reanudacion ya no sirve (ChangeStreamHistoryLost)"
            )

        # Al reanudar se entrega solo lo posterior al token, igual que hace Mongo con
        # `resumeAfter`: el panel no vuelve a recibir lo que ya pinto.
        if desde_token is None:
            pendientes = self._cambios
        else:
            pendientes = self._posteriores_a(desde_token)

        for cambio in pendientes:
            yield cambio

    def _posteriores_a(self, desde_token: Mapping[str, Any]) -> list[CambioAlerta]:
        for indice, cambio in enumerate(self._cambios):
            if cambio.token == desde_token:
                return self._cambios[indice + 1 :]
        return self._cambios
