"""Esquemas del portal del cliente (T-82): lo que se publica por el enlace del SMS.

Es lo unico que ve quien trae el enlace, y por eso es corto a proposito: monto, hora, las
cuatro ultimas cifras de SU cuenta, el destino enmascarado y el estado de su caso. Nada de
nombres, cedulas, puntajes, reglas ni notas de agentes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.modelos.base import MarcaTiempoUTC


class RespuestaDelCliente(BaseModel):
    valor: Literal["si_fui_yo", "no_fui_yo"]
    fecha: MarcaTiempoUTC


class EstadoDelCaso(BaseModel):
    """Donde va el caso, en palabras para una persona. El texto sale del servidor."""

    codigo: Literal["recibida", "en_revision", "en_investigacion", "fraude_confirmado", "sin_fraude"]
    titulo: str
    detalle: str


class AyudaAlCliente(BaseModel):
    telefono: str = Field(examples=["800-000-0000"])
    es_ejemplo: bool = Field(description="Numero ficticio del demo: la pagina lo rotula asi")


class TransferenciaDelCliente(BaseModel):
    monto_crc: int = Field(ge=0, description="Colones enteros")
    fecha: MarcaTiempoUTC
    cuenta_terminada_en: str = Field(examples=["4455"])
    destino_enmascarado: str = Field(examples=["6033-****"])


class EnlaceDelCliente(BaseModel):
    """GET y POST del enlace: la transferencia, lo que respondio y el estado de su caso."""

    fase: Literal["pendiente", "respondida"]
    puede_responder: bool
    transferencia: TransferenciaDelCliente
    vence: MarcaTiempoUTC
    respuesta: RespuestaDelCliente | None = None
    caso: EstadoDelCaso | None = None
    ayuda: AyudaAlCliente
    sondeo_segundos: int = Field(ge=1, description="Cada cuanto la pagina pide el estado")


class CuerpoRespuestaCliente(BaseModel):
    valor: Literal["si_fui_yo", "no_fui_yo"]


class MensajeSms(BaseModel):
    """Un SMS de la bandeja del simulador."""

    notificacion_id: str
    alerta_id: str
    para: str = Field(description="Telefono enmascarado")
    fecha: MarcaTiempoUTC
    estado_envio: Literal["pendiente", "enviado", "fallido"]
    texto: str | None = None
    ruta_enlace: str | None = Field(default=None, examples=["/c/..."])
    enlace: Literal["vigente", "usado", "vencido"] | None = None
    respuesta: RespuestaDelCliente | None = None


class BandejaSms(BaseModel):
    mensajes: list[MensajeSms]
