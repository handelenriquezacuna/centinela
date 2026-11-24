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
from app.deteccion.evaluador import CorteSeveridad
from app.modelos.alertas import Alerta
from app.modelos.base import AgenteId, AlertaId, MarcaTiempoUTC, ModeloCentinela
from app.modelos.estados import EstadoAlerta, Severidad
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
    """Lectura dual (H-26): cada elemento es v1 o v2 segun su `esquema_version`."""

    elementos: list[Alerta]


class ElementoTransaccion(BaseModel):
    """Una transaccion del historico con su alerta asociada (H-15).

    No expone `simulacion` (la etiqueta oculta del generador). `alerta_id` y
    `alerta_estado` son nulos cuando la transaccion no produjo alerta.
    """

    transaccion_id: str = Field(description="TXN-001")
    cuenta_origen: str = Field(description="Cuenta propia del banco, CTA-001")
    sinpe_origen: str | None = None
    cuenta_destino: str
    monto_crc: int = Field(ge=0, description="Colones enteros")
    moneda: str
    canal: str
    fecha: MarcaTiempoUTC
    alerta_id: str | None = None
    alerta_estado: EstadoAlerta | None = None


class PaginaTransacciones(Pagina):
    """Pagina de la busqueda forense. `plan` solo viene con `depurar=true`."""

    elementos: list[ElementoTransaccion]
    plan: dict[str, Any] | None = Field(
        default=None, description="Resumen del explain (executionStats) bajo depurar=true"
    )


class PeticionTransicion(BaseModel):
    """Mover una alerta por la maquina de estados (H-14).

    `estado_esperado` es opcional y sirve para que el portal diga "la vi en este
    estado": si ya no lo esta, 409 sin escribir. Aunque no venga, la escritura se
    condiciona al estado leido, asi que dos agentes no pueden pisarse.
    """

    model_config = ConfigDict(extra="forbid")

    destino: EstadoAlerta = Field(description="Estado al que pasa la alerta")
    agente_id: AgenteId | None = Field(
        default=None,
        deprecated=True,
        description="Ignorado: quien hace la accion es el agente de la sesion",
    )
    nota: str = Field(default="", max_length=1000, description="Queda en el historial")
    estado_esperado: EstadoAlerta | None = Field(
        default=None, description="Si viene y la alerta ya no esta asi, 409 sin escribir"
    )


class ResultadoTransicion(BaseModel):
    """La alerta como quedo y, si se escalo, el caso que se abrio con ella."""

    alerta: Alerta
    caso_id: str | None = Field(default=None, examples=["CAS-003"])


class PeticionLote(BaseModel):
    """Mover varias alertas al mismo estado (T-56), cada una por la maquina de estados.

    No es un `update_many`: cada alerta se valida y se escribe sola, con la misma funcion
    que `POST /alertas/{id}/transiciones`. La cantidad maxima la fija `app.lote_maximo`.
    """

    model_config = ConfigDict(extra="forbid")

    alertas: list[AlertaId] = Field(min_length=1, description="Identificadores, sin repetir")
    destino: EstadoAlerta = Field(description="Estado al que pasa cada alerta")
    nota: str = Field(default="", max_length=1000, description="Queda en el historial de cada una")


class ResultadoItemLote(BaseModel):
    """Que paso con una alerta del lote."""

    alerta_id: str = Field(examples=["ALR-001"])
    aplicada: bool
    estado_http: int = Field(description="200 si paso; si no, el estado que habria dado sola", examples=[200, 409])
    codigo: str | None = Field(default=None, description="Codigo de error estable, si no paso")
    mensaje: str = ""
    estado_actual: str | None = Field(default=None, description="Estado de la alerta tras el intento")
    caso_id: str | None = Field(default=None, description="Caso abierto, solo al escalar")


class ResultadoLote(BaseModel):
    """Cuantas pasaron y cuales no, con el motivo. Siempre 200 si el lote se proceso."""

    solicitadas: int
    aplicadas: int
    rechazadas: int
    resultados: list[ResultadoItemLote]


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


class LatenciaMs(BaseModel):
    """Latencia de deteccion de las ultimas mediciones (ventana movil), en milisegundos.

    Desde que la transaccion quedo insertada (hora del change stream) hasta que D1
    termino de escribir la alerta. Con `n == 0` los demas campos son nulos.
    """

    n: int = Field(ge=0, description="Mediciones en la ventana (como maximo 500)")
    p50: float | None = None
    p95: float | None = None
    max: float | None = None


class RezagoTarea(BaseModel):
    """Trabajo que a la tarea le falta por hacer (T-57), para D1: transacciones sin evaluar.

    `antiguedad_segundos` se mide contra `fecha` (hora del NEGOCIO) de la transaccion mas
    vieja sin evaluar, no contra la hora de insercion; es nulo si no hay pendientes. Lo
    que sirve para operar es `segundos_para_ponerse_al_dia`, que sale del ritmo medido
    de D1 (D-52). `medido_en` dice cuando se tomo la cifra: si deja de renovarse, D1
    esta atascado y `estado` lo dira.
    """

    pendientes: int = Field(ge=0, description="Transacciones insertadas y todavia sin evaluar")
    antiguedad_segundos: float | None = Field(
        default=None,
        description=(
            "Antiguedad de la mas vieja sin evaluar segun su fecha de negocio; "
            "nulo sin pendientes"
        ),
    )
    ritmo_por_segundo: float | None = Field(
        default=None,
        description="Transacciones evaluadas por segundo desde la medicion anterior",
    )
    segundos_para_ponerse_al_dia: float | None = Field(
        default=None,
        description="Estimado a ritmo constante; nulo sin ritmo medido o si D1 no avanza",
    )
    medido_en: datetime = Field(description="Cuando se tomo la medicion, en UTC")


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
    advertencia: str | None = Field(
        default=None,
        description=(
            "Condicion vigente que degrada la tarea aunque siga viva; por ejemplo D1 "
            "sin reglas activas (T-18)"
        ),
    )
    latencia_ms: LatenciaMs | None = Field(
        default=None,
        description="Solo D1: latencia de deteccion medida (H-09); nulo en las demas tareas",
    )
    rezago: RezagoTarea | None = Field(
        default=None,
        description=(
            "Solo D1: transacciones sin evaluar y antiguedad de la mas vieja (T-57); "
            "nulo en las demas tareas y antes de la primera medicion"
        ),
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
    advertencias: list[str] = Field(
        default_factory=list,
        description="Avisos de configuracion que no impiden operar (ej. clave de sesion efimera)",
    )

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
            advertencia=fila.advertencia,
            rezago=(
                RezagoTarea(
                    pendientes=fila.rezago.pendientes,
                    antiguedad_segundos=fila.rezago.antiguedad_segundos,
                    ritmo_por_segundo=fila.rezago.ritmo_por_segundo,
                    segundos_para_ponerse_al_dia=fila.rezago.segundos_para_ponerse_al_dia,
                    medido_en=fila.rezago.medido_en,
                )
                if fila.rezago is not None
                else None
            ),
            latencia_ms=(
                LatenciaMs(
                    n=fila.latencia.n,
                    p50=fila.latencia.p50,
                    p95=fila.latencia.p95,
                    max=fila.latencia.max,
                )
                if fila.latencia is not None
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Reglas (H-16)
# ---------------------------------------------------------------------------
#
# D-14: `Regla` (app/modelos) ya declara `versiones_anteriores`, asi que la API publica
# el modelo del dominio tal cual. Antes habia aca un `ReglaConHistoria` que lo
# extendia; con el campo en el modelo, la extension sobraba.

ParametrosRegla = dict[str, int | float]


class ListaReglas(BaseModel):
    """El catalogo completo. Son pocas reglas: no se pagina."""

    total: int = Field(ge=0)
    elementos: list[Regla]


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


# ---------------------------------------------------------------------------
# Politica de deteccion (T-20, D-16)
# ---------------------------------------------------------------------------


class VersionAnteriorPolitica(ModeloCentinela):
    """Una version de la politica que ya no rige, tal como estaba antes de editarse."""

    version: int = Field(ge=1)
    umbral_alerta: int
    puntaje_maximo: int
    cortes_severidad: list[CorteSeveridad]
    vigente_desde: MarcaTiempoUTC | None = None
    reemplazada_en: MarcaTiempoUTC


class PoliticaVigente(ModeloCentinela):
    """`politica_deteccion` vigente, con su historia. Es lo que lee D1 en cada evento."""

    politica_id: str = Field(alias="_id", examples=["POL-001"])
    descripcion: str
    version: int = Field(ge=1)
    umbral_alerta: int = Field(ge=0)
    puntaje_maximo: int = Field(ge=1)
    cortes_severidad: list[CorteSeveridad]
    notificacion: dict[str, Any] | None = None
    vigente_desde: MarcaTiempoUTC | None = None
    versiones_anteriores: list[VersionAnteriorPolitica] = Field(default_factory=list)


class CorteEntrada(_Peticion):
    """Un tramo de severidad propuesto. Limites inclusivos."""

    severidad: Severidad
    puntaje_minimo: int = Field(ge=0)
    puntaje_maximo: int = Field(ge=0)


class EdicionPolitica(_Peticion):
    """Edicion parcial de la politica. Los cortes se reemplazan enteros.

    Si se cambia `umbral_alerta` o `puntaje_maximo`, los cortes tienen que seguir
    cubriendo de uno al otro: se validan siempre contra el resultado final.
    """

    umbral_alerta: int | None = Field(default=None, ge=0)
    puntaje_maximo: int | None = Field(default=None, ge=1)
    cortes_severidad: list[CorteEntrada] | None = Field(default=None, min_length=1)
    version_esperada: int | None = Field(
        default=None,
        ge=1,
        description="Si viene y la politica ya no esta en esa version, 409 sin escribir",
    )

    @model_validator(mode="after")
    def _sin_nulos(self) -> Self:
        nulos = [campo for campo in self.model_fields_set if getattr(self, campo) is None]
        if nulos:
            raise ValueError(f"no se puede borrar un campo de la politica: {', '.join(nulos)}")
        return self

    def cambios(self) -> dict[str, Any]:
        """Los campos de la politica que vinieron, listos para guardar."""
        datos = self.model_dump(mode="json", exclude_unset=True, exclude={"version_esperada"})
        if "cortes_severidad" in datos:
            datos["cortes_severidad"] = sorted(
                datos["cortes_severidad"], key=lambda corte: corte["puntaje_minimo"]
            )
        return datos
