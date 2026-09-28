"""Cierre y metricas: notificaciones, indicadores_diarios.

Se corresponde con `scripts/practica1/d-cierre.js` (persona D).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from app.modelos.base import (
    AlertaId,
    DocumentoBase,
    FechaDia,
    MarcaTiempoUTC,
    NotificacionId,
)
from app.modelos.estados import EstadoAlerta, Severidad


class CanalNotificacion(StrEnum):
    CORREO = "correo"
    SMS = "sms"


class EstadoEnvio(StrEnum):
    PENDIENTE = "pendiente"
    ENVIADO = "enviado"
    FALLIDO = "fallido"


class Notificacion(DocumentoBase):
    """Registro de un aviso enviado por el disparador D2 (H-11).

    Existe para que un fallo de SMTP quede en la base y no solo en un log: si el
    envio falla, el documento queda `fallido` y se puede reintentar.
    """

    notificacion_id: NotificacionId = Field(alias="_id")
    alerta_id: AlertaId
    canal: CanalNotificacion = CanalNotificacion.CORREO
    estado_envio: EstadoEnvio = EstadoEnvio.PENDIENTE
    fecha: MarcaTiempoUTC
    detalle_error: str | None = None


class IndicadorDiario(DocumentoBase):
    """Indicadores de un dia, materializados por el pipeline `$merge` de H-22.

    Dos cosas fuera de lo comun, las dos a proposito:

    - El `_id` es la fecha (`2026-09-24`), no un identificador con prefijo. Es la
      clave natural de una coleccion materializada por dia, y hace que el `$merge`
      sea idempotente sin trabajo extra. El scaffold de la Practica 1 insertaba sin
      `_id`, lo que dejaba un ObjectId y rompia la regla de identificadores legibles.
    - `fecha` es texto `YYYY-MM-DD` y no ISODate. Es la unica excepcion declarada a
      la regla de fechas: un indicador es de un dia calendario de Costa Rica, no de
      un instante, y guardarlo como ISODate obliga a decidir a que hora empieza el
      dia cada vez que se consulta.

    `nada se recalcula dentro de una peticion HTTP`: el tablero solo lee esto, y
    `calculado_en` le dice al supervisor que tan fresco es lo que ve.
    """

    fecha: FechaDia = Field(
        alias="_id", description="El _id ES la fecha YYYY-MM-DD: un documento por dia"
    )
    total_alertas: int = Field(ge=0)
    por_severidad: dict[Severidad, int] = Field(default_factory=dict)
    por_estado: dict[EstadoAlerta, int] = Field(default_factory=dict)
    # Opcional porque el validador todavia no la declara.
    calculado_en: MarcaTiempoUTC | None = None

    @model_validator(mode="after")
    def completar_los_conteos(self) -> "IndicadorDiario":
        """Rellena con 0 los estados y severidades que no aparecen.

        El validador de la coleccion exige las cinco claves de estado y las tres de
        severidad, siempre. Y tiene razon: un tablero que tiene que distinguir "cero
        alertas descartadas" de "la clave no vino" es un tablero que se va a equivocar.
        Completar aca es mejor que exigirle a cada pipeline que se acuerde.
        """
        for severidad in Severidad:
            self.por_severidad.setdefault(severidad, 0)
        for estado in EstadoAlerta:
            self.por_estado.setdefault(estado, 0)
        return self

    def a_documento(self) -> dict:
        """El documento lleva la fecha DOS veces: como `_id` y como `fecha`.

        No es un descuido. El `_id` es la clave natural que hace idempotente el
        `$merge` de H-22, y el campo `fecha` es el que declara el validador y el que
        indexa el contrato de IDs. Son el mismo valor y esta prueba lo sostiene:
        `prueba_la_unica_excepcion_es_la_fecha_de_indicadores`.
        """
        documento = super().a_documento()
        documento["fecha"] = documento["_id"]
        return documento
