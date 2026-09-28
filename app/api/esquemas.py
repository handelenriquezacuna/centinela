"""Esquemas de respuesta de la API.

Estan separados de `app/modelos/` a proposito: `app/modelos/` describe el documento de
Mongo y `app/api/esquemas.py` describe lo que se publica por HTTP. Hoy son casi lo
mismo; cuando dejen de serlo (un campo interno que no se expone, un campo calculado
que no se guarda), no habra que romper nada para separarlos.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modelos.alertas import Alerta


class Pagina(BaseModel):
    """Envoltura de toda respuesta paginada de Centinela.

    `total` es el total que cumple el filtro, no el tamano de la pagina: el agente
    necesita saber cuantas alertas tiene en su cola.

    `limite` es el que de verdad se aplico despues de acotarlo con
    `app.pagina_maxima`, que puede ser menor que el que pidio el cliente. Va en la
    respuesta justamente para que el cliente pueda notarlo.
    """

    total: int = Field(ge=0, description="Documentos que cumplen el filtro")
    limite: int = Field(ge=1, description="Limite aplicado, ya acotado por app.pagina_maxima")
    desplazamiento: int = Field(ge=0, description="Documentos omitidos desde el inicio")


class PaginaAlertas(Pagina):
    elementos: list[Alerta]


class Salud(BaseModel):
    """Respuesta de `/salud`: vivacidad del proceso y estado de la base.

    `/salud` responde "el proceso esta vivo y la base contesta". Es distinta de
    `/estado`, que responde "el sistema esta haciendo su trabajo" (los disparadores
    D1/D2/D3, el ultimo evento procesado, el retraso) y que se construye en H-00B.
    Mezclarlas es lo que hace que un sistema se declare sano mientras el motor lleva
    dos horas sin procesar una transaccion.
    """

    estado: str = Field(examples=["ok", "degradado"])
    version: str
    entorno: str
    mongo_conectado: bool
    replica_set: str | None = None
    es_primario: bool | None = None
    detalle: str | None = Field(
        default=None, description="Motivo, cuando la base no responde"
    )
