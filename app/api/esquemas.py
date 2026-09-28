"""Esquemas de respuesta de la API.

Estan separados de `app/modelos/` a proposito: `app/modelos/` describe el documento de
Mongo y `app/api/esquemas.py` describe lo que se publica por HTTP. Hoy son casi lo
mismo; cuando dejen de serlo (un campo interno que no se expone, un campo calculado
que no se guarda), no habra que romper nada para separarlos.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.modelos.alertas import Alerta
from app.supervision import (
    EstadoSupervision,
    EstadoTarea,
    InformeSupervision,
    InformeTarea,
)


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


class ErrorDeTarea(BaseModel):
    """Ultimo error de una tarea de fondo, con cuando ocurrio."""

    mensaje: str = Field(examples=["ConnectionError: la base no responde"])
    fecha: datetime


class TareaSupervisada(BaseModel):
    """Una tarea de fondo tal como la publica `/estado`.

    El mismo juego de campos para el latido de la plataforma y para D1/D2/D3 cuando
    existan. Que sea uno solo es el punto: quien mire `/estado` no tiene que aprender
    un formato distinto por disparador.
    """

    nombre: str = Field(examples=["latido", "d1_deteccion"])
    estado: EstadoTarea
    eventos_procesados: int = Field(
        ge=0, description="Eventos que esta tarea termino de procesar desde que arranco"
    )
    ultimo_evento: datetime | None = Field(
        default=None, description="Cuando se proceso el ultimo evento, en UTC"
    )
    retraso_segundos: float | None = Field(
        default=None,
        description="Segundos desde el ultimo evento procesado; nulo si todavia no hubo ninguno",
    )
    ultimo_error: ErrorDeTarea | None = None
    historia: str | None = Field(
        default=None,
        description="La historia que construye esta tarea, cuando todavia no existe",
        examples=["H-09"],
    )


class EstadoFuncional(BaseModel):
    """Respuesta de `/estado`: el sistema esta HACIENDO su trabajo, o no.

    Distinta de `/salud` a proposito, y la diferencia es la falla que arruina una
    demo: un proceso vivo con la base contestando responde `/salud` 200 mientras el
    detector lleva dos horas sin procesar una transaccion. `/estado` es la ruta que
    puede decirlo.

    Los disparadores que todavia no existen se reportan como `no_construida` con la
    historia que los trae, y NO degradan el sistema: no estar construido todavia es
    una verdad del calendario, no una falla.
    """

    estado: EstadoSupervision
    version: str
    entorno: str
    tolerancia_retraso_segundos: float = Field(
        gt=0, description="Silencio maximo tolerado antes de declarar una tarea retrasada"
    )
    tareas: list[TareaSupervisada]

    @classmethod
    def desde_informe(
        cls, informe: InformeSupervision, *, version: str, entorno: str
    ) -> EstadoFuncional:
        """Traduce el informe del supervisor a la respuesta HTTP.

        La traduccion vive aca y no en `app/supervision/` para que el supervisor no
        tenga que saber que existe una API. Es la misma regla del motor: el que hace
        el trabajo no conoce la pantalla.
        """
        return cls(
            estado=informe.estado,
            version=version,
            entorno=entorno,
            tolerancia_retraso_segundos=informe.tolerancia_retraso_segundos,
            tareas=[cls._tarea(fila) for fila in informe.tareas],
        )

    @staticmethod
    def _tarea(fila: InformeTarea) -> TareaSupervisada:
        return TareaSupervisada(
            nombre=fila.nombre,
            estado=fila.estado,
            eventos_procesados=fila.eventos_procesados,
            ultimo_evento=fila.ultimo_evento,
            retraso_segundos=fila.retraso_segundos,
            ultimo_error=(
                ErrorDeTarea(
                    mensaje=fila.ultimo_error.mensaje, fecha=fila.ultimo_error.fecha
                )
                if fila.ultimo_error is not None
                else None
            ),
            historia=fila.historia,
        )
