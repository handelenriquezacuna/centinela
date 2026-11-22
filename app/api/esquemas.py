"""Esquemas de respuesta de la API.

Estan separados de `app/modelos/` a proposito: `app/modelos/` describe el documento de
Mongo y `app/api/esquemas.py` describe lo que se publica por HTTP. Hoy son casi lo
mismo; cuando dejen de serlo (un campo interno que no se expone, un campo calculado
que no se guarda), no habra que romper nada para separarlos.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.deteccion import ContextoEvaluacion, PoliticaDeteccion
from app.modelos.alertas import Alerta
from app.modelos.base import MarcaTiempoUTC, ModeloCentinela
from app.modelos.transaccional import Regla, Transaccion
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


# ---------------------------------------------------------------------------
# Reglas (H-16)
# ---------------------------------------------------------------------------
#
# `Regla` (app/modelos) describe el documento tal como lo lee el motor e ignora
# `versiones_anteriores`. La API la publica completa, con su historia, sin tocar el
# modelo del dominio: por eso `ReglaConHistoria` extiende `Regla` aca.

ParametrosRegla = dict[str, int | float]


class VersionAnteriorRegla(ModeloCentinela):
    """Una version que ya no rige, tal como estaba justo antes de editarse.

    Con esto una alerta vieja se explica con la regla con que se evaluo: la alerta
    guarda `regla_id` y `version`, y la version se busca aca si ya no es la vigente.
    """

    codigo: str
    descripcion: str
    tipo: str
    umbral: float | None = None
    parametros: ParametrosRegla = Field(default_factory=dict)
    peso: int
    activa: bool
    version: int = Field(ge=1)
    esquema_version: int = Field(ge=1)
    reemplazada_en: MarcaTiempoUTC = Field(description="Cuando dejo de regir, en UTC")


class ReglaConHistoria(Regla):
    versiones_anteriores: list[VersionAnteriorRegla] = Field(
        default_factory=list,
        description="Versiones anteriores, de la mas vieja a la mas reciente",
    )


class ListaReglas(BaseModel):
    """El catalogo completo. Son pocas reglas: no se pagina."""

    total: int = Field(ge=0)
    elementos: list[ReglaConHistoria]


class _Peticion(ModeloCentinela):
    """Cuerpo de una peticion: un campo desconocido es un error, no se ignora.

    Un `tipo` o un `codigo` en una edicion no se puede aplicar; tragarlo en silencio
    haria creer al supervisor que lo cambio.
    """

    model_config = ConfigDict(extra="forbid")


class NuevaRegla(_Peticion):
    """Alta de una regla. El `_id` (REG-NNN) y `version: 1` los pone el servidor."""

    codigo: str = Field(pattern=r"^R-\d{2}$", examples=["R-13"])
    descripcion: str = Field(min_length=1)
    tipo: str = Field(description="Uno de GET /api/v1/reglas/tipos", examples=["monto"])
    umbral: float = Field(ge=0, description="Su unidad depende del tipo")
    parametros: ParametrosRegla = Field(default_factory=dict)
    peso: int = Field(ge=0, le=100)
    activa: bool = True


class EdicionRegla(_Peticion):
    """Edicion parcial. Lo que no viene no cambia; `parametros` se reemplaza entero.

    Desactivar es `{"activa": false}`: una edicion mas, y queda versionada igual.
    """

    descripcion: str | None = Field(default=None, min_length=1)
    umbral: float | None = Field(default=None, ge=0)
    parametros: ParametrosRegla | None = None
    peso: int | None = Field(default=None, ge=0, le=100)
    activa: bool | None = None
    version_esperada: int | None = Field(
        default=None,
        ge=1,
        description="Si viene y la regla ya no esta en esa version, 409 sin escribir",
    )

    @model_validator(mode="after")
    def _sin_nulos(self) -> Self:
        nulos = [campo for campo in self.model_fields_set if getattr(self, campo) is None]
        if nulos:
            raise ValueError(f"no se puede borrar un campo de la regla: {', '.join(nulos)}")
        return self

    def cambios(self) -> dict[str, Any]:
        """Los campos de la regla que vinieron en la peticion."""
        return self.model_dump(exclude_unset=True, exclude={"version_esperada"})


class ParametroTipo(BaseModel):
    nombre: str = Field(examples=["ventana_minutos"])
    descripcion: str


class TipoSoportado(BaseModel):
    """Un tipo que el motor sabe despachar, con lo que lee de la regla."""

    tipo: str = Field(examples=["velocidad"])
    umbral: str = Field(description="Que significa el umbral para este tipo")
    parametros: list[ParametroTipo]
    evaluable: bool = Field(
        description="Falso si el motor todavia no lo evalua en linea (red_mulas, H-23)"
    )


class ListaTipos(BaseModel):
    elementos: list[TipoSoportado]


class PeticionSimulacion(BaseModel):
    """Evaluar una transaccion sin escribir nada.

    `reglas` se superpone al catalogo de la base por `_id`: una regla que viene
    reemplaza a la guardada con ese `_id`, una con `_id` nuevo se agrega, y las demas
    quedan como estan. Asi se prueba un umbral mandando solo la regla que cambia.
    `politica`, si viene, reemplaza a la vigente.
    """

    model_config = ConfigDict(extra="forbid")

    transaccion: Transaccion
    contexto: ContextoEvaluacion
    reglas: list[Regla] | None = None
    politica: PoliticaDeteccion | None = None
