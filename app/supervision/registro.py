"""El registro de tareas de fondo y su vigilancia.

No hay hilos ni procesos: las tareas son `asyncio.Task` del lifespan, porque el
monolito es un solo proceso (`uvicorn app.main:app`, un worker). Un worker y no dos
es parte del contrato: dos procesos escuchando el mismo change stream crean la alerta
dos veces.

Que vigila el supervisor, y por que cada cosa:

- **Que la tarea siga viva.** Una `asyncio.Task` que revienta muere en silencio: si
  nadie llama a `task.exception()`, el error aparece en el registro cuando el
  recolector de basura se lleva la tarea, o no aparece. Aca el error se recoge en el
  momento con un `done_callback` y queda en el informe.
- **Que la tarea siga avanzando.** Una tarea viva pero atascada (un cursor que no
  devuelve nada, una espera que nunca termina) es el caso peor: parece sana. Por eso
  cada tarea reporta un latido por evento procesado y el informe mira el retraso
  contra `tolerancia_segundos`.

El retraso se mide con `time.monotonic()` y no con el reloj de pared: un ajuste de
hora del sistema no puede inventar ni borrar un retraso. La fecha de pared se guarda
aparte, y es solo para mostrar.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

registro_log = logging.getLogger("centinela.supervision")

# Los disparadores que el sistema promete y que todavia no existen, con la historia
# que trae cada uno. Se publican en `/estado` como `no_construida`.
#
# Por que esta lista existe en vez de tres tareas de mentira: el criterio de H-00B
# pide que `/estado` reporte D1/D2/D3, y la forma honesta de reportar algo que no
# existe es decir que no existe. Un disparador simulado responderia "corriendo"
# mientras no hace nada, que es exactamente la mentira que `/estado` viene a evitar.
DISPARADORES_PREVISTOS: dict[str, str] = {
    "d1_deteccion": "H-09",
    "d2_notificacion": "H-11",
    "d3_indicadores": "H-22",
}


class EstadoTarea(StrEnum):
    """Estado de una tarea supervisada. El valor es lo que sale por HTTP."""

    CORRIENDO = "corriendo"
    RETRASADA = "retrasada"
    DETENIDA = "detenida"
    FALLIDA = "fallida"
    NO_CONSTRUIDA = "no_construida"


class EstadoSupervision(StrEnum):
    """Resumen del sistema.

    `sin_supervision` no es un detalle: si nadie registro una tarea, el sistema no
    puede afirmar que esta haciendo su trabajo, y decir `ok` seria mentir. Pasa
    cuando se arma la aplicacion sin lifespan (algunas pruebas) o si un cambio futuro
    se lleva el lanzamiento de las tareas sin que nadie lo note.
    """

    OK = "ok"
    DEGRADADO = "degradado"
    SIN_SUPERVISION = "sin_supervision"


@dataclass(frozen=True)
class ErrorRegistrado:
    """Ultimo error de una tarea. Se guarda el texto, no la excepcion viva."""

    mensaje: str
    fecha: datetime


@dataclass
class _Pulso:
    """Lo que el supervisor sabe de una tarea. Mutable y privado del modulo."""

    tarea: asyncio.Task[Any] | None
    historia: str | None = None
    eventos_procesados: int = 0
    ultimo_evento: datetime | None = None
    ultimo_error: ErrorRegistrado | None = None
    # Referencias monotonas: para medir el retraso sin depender del reloj de pared.
    registrada_en: float = field(default_factory=time.monotonic)
    ultimo_evento_monotono: float | None = None


@dataclass(frozen=True)
class InformeTarea:
    """Una fila del informe de `/estado`."""

    nombre: str
    estado: EstadoTarea
    eventos_procesados: int
    ultimo_evento: datetime | None
    retraso_segundos: float | None
    ultimo_error: ErrorRegistrado | None
    historia: str | None


@dataclass(frozen=True)
class InformeSupervision:
    """Lo que `/estado` publica. Se arma en el momento de la peticion."""

    estado: EstadoSupervision
    tolerancia_retraso_segundos: float
    tareas: tuple[InformeTarea, ...]

    def tarea(self, nombre: str) -> InformeTarea:
        """Una fila por nombre. Util en pruebas y en el registro de eventos."""
        for informe in self.tareas:
            if informe.nombre == nombre:
                return informe
        raise KeyError(nombre)


class Supervisor:
    """Lanza, vigila y reporta las tareas de fondo del proceso.

    Se crea una sola vez en el lifespan y se guarda en `app.state`. La capa HTTP lo
    recibe por dependencia; nunca lo construye.
    """

    def __init__(self, *, tolerancia_segundos: float) -> None:
        if tolerancia_segundos <= 0:
            raise ValueError("la tolerancia de retraso tiene que ser positiva")
        self._tolerancia = float(tolerancia_segundos)
        self._pulsos: dict[str, _Pulso] = {}

    # -- Registro ---------------------------------------------------------

    def lanzar(self, nombre: str, corrutina: Coroutine[Any, Any, None]) -> asyncio.Task[Any]:
        """Crea la tarea, la registra y devuelve el `asyncio.Task`.

        El `done_callback` recoge el error en el momento en que la tarea muere. Sin
        eso, una tarea que revienta deja "Task exception was never retrieved" en el
        registro cuando el recolector de basura pase, y `/estado` no sabria por que
        se cayo.
        """
        if nombre in self._pulsos:
            # Se cierra la corrutina que no se va a ejecutar: una corrutina creada y
            # nunca esperada deja un RuntimeWarning suelto que aparece muy lejos del
            # lugar donde se cometio el error.
            corrutina.close()
            raise ValueError(f"la tarea {nombre} ya esta registrada")

        tarea = asyncio.create_task(corrutina, name=f"centinela:{nombre}")
        self._pulsos[nombre] = _Pulso(
            tarea=tarea, historia=DISPARADORES_PREVISTOS.get(nombre)
        )
        tarea.add_done_callback(lambda terminada: self._al_terminar(nombre, terminada))
        registro_log.info("tarea supervisada arriba: %s", nombre)
        return tarea

    def _al_terminar(self, nombre: str, tarea: asyncio.Task[Any]) -> None:
        if tarea.cancelled():
            registro_log.info("tarea supervisada detenida: %s", nombre)
            return
        fallo = tarea.exception()
        if fallo is not None:
            self.anotar_error(nombre, fallo)
            registro_log.error("tarea supervisada caida: %s (%s)", nombre, fallo)

    # -- Lo que reportan las tareas ---------------------------------------

    def latir(self, nombre: str) -> None:
        """Un evento procesado. Es lo que las tareas llaman en cada vuelta.

        Para D1 (H-09) "un evento" es una transaccion evaluada; para el latido de la
        plataforma es una comprobacion completa. El contrato de `/estado` es el
        mismo: `ultimo_evento` es lo ultimo que esta tarea termino de procesar.
        """
        pulso = self._pulsos[nombre]
        pulso.eventos_procesados += 1
        pulso.ultimo_evento = datetime.now(UTC)
        pulso.ultimo_evento_monotono = time.monotonic()

    def anotar_error(self, nombre: str, fallo: BaseException) -> None:
        """Deja el ultimo error de una tarea sin matarla.

        Un fallo de SMTP o un corte de red no deben tumbar el proceso; si deben ser
        visibles. El error no degrada por si mismo: lo que degrada es dejar de
        procesar. Un error transitorio del que la tarea se recupero no puede dejar el
        sistema en rojo para siempre.
        """
        pulso = self._pulsos.get(nombre)
        if pulso is None:  # pragma: no cover - solo si alguien anota antes de lanzar
            return
        pulso.ultimo_error = ErrorRegistrado(
            mensaje=f"{type(fallo).__name__}: {fallo}", fecha=datetime.now(UTC)
        )

    # -- Informe ----------------------------------------------------------

    def informe(self) -> InformeSupervision:
        """Arma el informe completo: tareas registradas y disparadores previstos."""
        ahora = time.monotonic()
        filas = [self._fila(nombre, pulso, ahora) for nombre, pulso in self._pulsos.items()]

        for nombre, historia in DISPARADORES_PREVISTOS.items():
            if nombre in self._pulsos:
                continue
            filas.append(
                InformeTarea(
                    nombre=nombre,
                    estado=EstadoTarea.NO_CONSTRUIDA,
                    eventos_procesados=0,
                    ultimo_evento=None,
                    retraso_segundos=None,
                    ultimo_error=None,
                    historia=historia,
                )
            )

        return InformeSupervision(
            estado=self._resumen(filas),
            tolerancia_retraso_segundos=self._tolerancia,
            tareas=tuple(filas),
        )

    def _fila(self, nombre: str, pulso: _Pulso, ahora: float) -> InformeTarea:
        referencia = (
            pulso.ultimo_evento_monotono
            if pulso.ultimo_evento_monotono is not None
            else pulso.registrada_en
        )
        # El retraso solo tiene sentido cuando ya hubo un evento. Antes del primero,
        # lo que se mide contra la tolerancia es el tiempo desde que arranco: una
        # tarea recien lanzada no esta retrasada, tiene su ventana de gracia.
        transcurrido = ahora - referencia
        retraso = transcurrido if pulso.ultimo_evento_monotono is not None else None

        return InformeTarea(
            nombre=nombre,
            estado=self._estado_de(pulso, transcurrido),
            eventos_procesados=pulso.eventos_procesados,
            ultimo_evento=pulso.ultimo_evento,
            retraso_segundos=round(retraso, 3) if retraso is not None else None,
            ultimo_error=pulso.ultimo_error,
            historia=pulso.historia,
        )

    def _estado_de(self, pulso: _Pulso, transcurrido: float) -> EstadoTarea:
        tarea = pulso.tarea
        if tarea is None:  # pragma: no cover - reservado para tareas externas
            return EstadoTarea.NO_CONSTRUIDA
        if tarea.done():
            # Cancelada a mano o por el cierre del lifespan: detenida, no fallida.
            return EstadoTarea.DETENIDA if tarea.cancelled() else EstadoTarea.FALLIDA
        if transcurrido > self._tolerancia:
            return EstadoTarea.RETRASADA
        return EstadoTarea.CORRIENDO

    def _resumen(self, filas: list[InformeTarea]) -> EstadoSupervision:
        """`ok` solo si TODA tarea registrada esta corriendo.

        Los disparadores previstos (`no_construida`) no degradan: no estar construido
        todavia es una verdad del calendario, no una falla del sistema. El dia que
        H-09 registre `d1_deteccion`, su fila deja de ser prevista y pasa a
        vigilarse como cualquier otra.
        """
        registradas = [fila for fila in filas if fila.estado != EstadoTarea.NO_CONSTRUIDA]
        if not registradas:
            return EstadoSupervision.SIN_SUPERVISION
        if all(fila.estado == EstadoTarea.CORRIENDO for fila in registradas):
            return EstadoSupervision.OK
        return EstadoSupervision.DEGRADADO

    # -- Cierre -----------------------------------------------------------

    async def detener_tarea(self, nombre: str) -> None:
        """Cancela una sola tarea y espera a que muera. No la desregistra.

        Sirve para apagar un disparador sin bajar el proceso -por ejemplo, detener D1
        mientras se recarga el catalogo de reglas- y es la forma de comprobar el
        criterio de la historia: una tarea muerta tiene que dejar `/estado` en
        `degradado`, no desaparecer del informe como si nunca hubiera existido.
        """
        pulso = self._pulsos[nombre]
        if pulso.tarea is None or pulso.tarea.done():
            return
        pulso.tarea.cancel()
        try:
            await pulso.tarea
        except asyncio.CancelledError:
            pass

    async def detener(self) -> None:
        """Cancela las tareas y espera a que terminen. La llama el lifespan.

        Se espera a proposito: sin el `gather`, el proceso puede terminar con las
        tareas a medio cancelar y el cliente de Mongo cerrandose debajo de ellas, lo
        que produce excepciones al apagar que no significan nada.
        """
        tareas = [pulso.tarea for pulso in self._pulsos.values() if pulso.tarea is not None]
        for tarea in tareas:
            tarea.cancel()
        if tareas:
            await asyncio.gather(*tareas, return_exceptions=True)
        registro_log.info("tareas supervisadas detenidas: %d", len(tareas))
