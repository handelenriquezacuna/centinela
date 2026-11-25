"""H-09 · Disparador D1: transaccion -> alerta.

Escucha `transacciones` por change stream (solo inserts) y, por cada transaccion:

    contexto (repo) -> evaluar (H-08, puro) -> alerta si supera el umbral -> token

El contrato, y por que cada pieza:

- **Primero la alerta, despues el token.** El token se guarda solo cuando las
  escrituras del evento terminaron. Si el proceso muere entre las dos, al reanudar se
  reprocesa el evento: el change stream entrega *al menos una vez*.
- **La no duplicacion la impone la base, no este codigo.** El reproceso choca con el
  indice unico de `alertas.transaccion_id` y se cuenta como "ya procesada". Una
  transaccion produce una sola alerta; si disparan tres reglas, van las tres dentro.
- **Reanudacion con el mismo pipeline y opciones** (`resumeAfter`). Las constantes
  viven en `app/repos/deteccion.py` y las dos aperturas las comparten.
- **Token inservible** (`ChangeStreamHistoryLost`, un token corrupto, o un
  `invalidate` porque alguien borro `transacciones`, como hace re-sembrar con
  `01-colecciones.js`): se registra como `resincronizar` y D1 arranca desde ahora. Lo que entro entre el token y ahora
  NO se evalua; queda en el registro y en `/estado` como ultimo error, para que nadie
  lo descubra tarde.
- **Reintentos con espera progresiva** (`deteccion` en el YAML). Un evento que agota
  los reintentos queda en el descarte (`deteccion_estado`, clase `descarte`) y D1
  sigue: un evento envenenado no puede detener la deteccion de todos los demas. Si ni
  el descarte se puede escribir (la base no esta), el error sube, el token no avanza y
  D1 reabre desde el ultimo token guardado con la misma espera progresiva.
- **Latencia medida.** Desde que Mongo inserto la transaccion (la hora del evento del
  change stream, `wallTime`; NO `Transaccion.fecha`, que es la hora del negocio y puede
  ser historica) hasta que la alerta quedo escrita. Se guarda una ventana movil de las
  ultimas 500 en el supervisor y `/estado` publica p50, p95, max y n. Solo se miden
  las alertas nuevas: un reproceso o una transaccion sin alerta no tienen una escritura
  que medir. Los dos relojes son el del servidor de Mongo y el de este proceso; en
  produccion se asume sincronizados (NTP) y un desfase negativo se registra como 0.
- **Rezago medido** (T-57). Cada `rezago_intervalo_segundos`, y despues de guardar el
  token, D1 pregunta cuantas transacciones hay posteriores a la ultima que termino y la
  antiguedad de la mas vieja (consulta por indice de `_id`, en `app/repos/`). `/estado`
  lo publica y el panel avisa si pasa el umbral. Es la unica forma de ver que D1, que
  evalua de a una, se esta quedando atras ante una carga por lotes. Junto al conteo se
  publica `atraso_segundos` (ahora menos la insercion del ultimo evento procesado; 0 si la
  vuelta del cursor llega vacia): una red de seguridad que no depende del orden de ids.
- **Por lotes** (T-62). D1 toma del change stream lo que haya, hasta
  `deteccion.lote_maximo` eventos y SIN esperar a llenar el lote: con poco trafico un evento
  sale solo. Por lote lee reglas y politica una vez, el contexto de todas las transacciones
  con lecturas agrupadas, evalua una por una en el orden del stream (con la fecha de cada
  una, ver `app/deteccion/contexto_lote.py`), escribe las alertas con un
  `insert_many(ordered=False)` y guarda el token UNA vez, despues de las alertas. Tras una
  caida a mitad de lote se reprocesa el lote entero: el indice unico rechaza los duplicados
  y no se pierde nada. Un evento que falla no arrastra al lote: se aisla y se procesa solo,
  con los reintentos y el descarte de siempre. Un lote de un evento es el camino por evento
  de antes, sin cambios.
- **Latido por vuelta**, con o sin evento. `/estado` mide el retraso desde el ultimo
  latido: un D1 sano sin trafico late cada `espera_cursor_ms`; uno atascado deja de
  latir y pasa a `retrasada`.

Este modulo no consulta Mongo (todo pasa por `ProtocoloRepoDeteccion`) y no importa
`app/api/`: el motor no sabe que existe una pantalla.

Un solo D1 por base: dos procesos escuchando el mismo change stream evaluan dos veces
cada transaccion (el indice unico evita la alerta doble, pero no el trabajo doble ni
la carrera por el token). Por eso el monolito corre con un solo worker.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import aclosing, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from app.deteccion.evaluador import Evaluacion, evaluar
from app.modelos import (
    ESQUEMA_ALERTA_V2,
    ESTADO_INICIAL,
    AccionHistorial,
    Alerta,
    PoliticaAplicada,
    ReglaAplicada,
    Transaccion,
)
from app.nucleo.config import AjustesDeteccion
from app.repos.deteccion import DatosOrigen, EventoTransaccion, ProtocoloRepoDeteccion
from app.repos.flujo_alertas import TokenDeReanudacionInvalido
from app.supervision import Supervisor

registro_log = logging.getLogger("centinela.deteccion")

# Clave de `DISPARADORES_PREVISTOS`: asi `/estado` reconoce a D1 como el disparador
# prometido por H-09, y es tambien el `_id` del token en `deteccion_estado`.
NOMBRE_D1 = "d1_deteccion"

PREFIJO_TRANSACCION = "TXN-"
PREFIJO_ALERTA = "ALR-"

Reloj = Callable[[], datetime]

MOTIVO_SIN_REGLAS = (
    "sin reglas activas en reglas_deteccion: ninguna transaccion puede generar alerta "
    "(sembrar scripts/semilla/03-catalogos.js o reactivar una regla)"
)


def _ahora() -> datetime:
    return datetime.now(UTC)


class Resultado(StrEnum):
    """Que paso con una transaccion. Es lo que D1 registra por evento."""

    ALERTA_CREADA = "alerta_creada"
    YA_PROCESADA = "ya_procesada"
    SIN_ALERTA = "sin_alerta"


# ---------------------------------------------------------------------------
# Partes puras
# ---------------------------------------------------------------------------


def alerta_id_para(transaccion_id: str) -> str:
    """`TXN-001` -> `ALR-001`. El identificador de la alerta sale de la transaccion.

    Derivado y no secuencial a proposito: es idempotente (reprocesar la misma
    transaccion produce el mismo `_id`, que tambien choca), no necesita un contador
    con su escritura extra y su carrera, y coincide con `CONTRATO-IDS.md`, donde
    ALR-001 es la alerta de TXN-001.
    """
    if not transaccion_id.startswith(PREFIJO_TRANSACCION):
        raise ValueError(f"identificador de transaccion inesperado: {transaccion_id}")
    return PREFIJO_ALERTA + transaccion_id.removeprefix(PREFIJO_TRANSACCION)


def nota_de_creacion(evaluacion: Evaluacion) -> str:
    """Primera entrada del historial, para la persona que lee la bitacora.

    Desde H-26 la alerta es v2: la version de cada regla y la politica van como datos
    en `reglas_disparadas` y `politica`, no como texto. La nota queda corta y legible,
    con la politica y los codigos, y no es la fuente de nada.
    """
    codigos = ", ".join(
        f"{disparada.codigo} v{disparada.version}" for disparada in evaluacion.reglas_disparadas
    )
    return (
        f"Creada por D1 con {evaluacion.politica_id} v{evaluacion.politica_version}; "
        f"reglas: {codigos}"
    )


def reglas_aplicadas(evaluacion: Evaluacion) -> list[ReglaAplicada]:
    """`ReglaDisparada` del evaluador -> copia embebida v2 de la alerta. Sin perder nada."""
    return [
        ReglaAplicada(
            regla_id=disparada.regla_id,
            codigo=disparada.codigo,
            version=disparada.version,
            peso=disparada.peso,
            explicacion=disparada.explicacion,
        )
        for disparada in evaluacion.reglas_disparadas
    ]


def construir_alerta(
    transaccion: Transaccion,
    evaluacion: Evaluacion,
    origen: DatosOrigen,
    ahora: datetime,
) -> Alerta | None:
    """La alerta v2 que corresponde a la evaluacion, o `None` si no supera el umbral."""
    if not evaluacion.genera_alerta:
        return None
    if evaluacion.severidad is None:
        # La politica dice que hay alerta pero ningun corte contiene el puntaje: es una
        # politica mal formada, no una transaccion sana. Se reporta, no se adivina.
        raise ValueError(
            f"puntaje {evaluacion.puntaje} genera alerta pero no cae en ningun corte de "
            f"{evaluacion.politica_id} v{evaluacion.politica_version}"
        )
    return Alerta(
        _id=alerta_id_para(transaccion.transaccion_id),
        esquema_version=ESQUEMA_ALERTA_V2,
        transaccion_id=transaccion.transaccion_id,
        reglas_disparadas=reglas_aplicadas(evaluacion),
        politica=PoliticaAplicada(
            politica_id=evaluacion.politica_id, version=evaluacion.politica_version
        ),
        puntaje=evaluacion.puntaje,
        severidad=evaluacion.severidad,
        estado=ESTADO_INICIAL,
        fecha_creacion=ahora,
        historial=[
            AccionHistorial(
                accion=ESTADO_INICIAL,
                agente_id=None,
                fecha=ahora,
                nota=nota_de_creacion(evaluacion),
            )
        ],
        cuenta_origen=origen.cuenta_sinpe,
        cuenta_destino=transaccion.cuenta_destino,
        monto_crc=transaccion.monto_crc,
        moneda=transaccion.moneda,
        cliente_nombre=origen.cliente_nombre,
    )


def vigilar_catalogo(supervisor: Supervisor, nombre: str, reglas_activas: int) -> None:
    """T-18: sin reglas activas D1 late pero no detecta nada, y `/estado` lo dice.

    Pasa si se re-siembra `01-colecciones.js` sin correr despues `03-catalogos.js`, o si
    un supervisor apaga todas las reglas desde la API. El proceso esta sano; el sistema
    no esta haciendo su trabajo, y eso es exactamente lo que `/estado` viene a decir.
    """
    supervisor.advertir(nombre, MOTIVO_SIN_REGLAS if reglas_activas == 0 else None)


def espera_progresiva(intento: int, ajustes: AjustesDeteccion) -> float:
    """Segundos a esperar antes del intento siguiente: se duplica hasta el tope."""
    return min(
        ajustes.espera_inicial_segundos * (2**intento), ajustes.espera_maxima_segundos
    )


# ---------------------------------------------------------------------------
# Un evento
# ---------------------------------------------------------------------------


async def procesar_transaccion(
    repo: ProtocoloRepoDeteccion,
    documento: Mapping[str, Any],
    *,
    ahora: datetime,
    al_leer_reglas: Callable[[int], None] | None = None,
) -> Resultado:
    """Contexto -> evaluar -> alerta. No toca el token: eso es de quien llama.

    `al_leer_reglas` recibe cuantas reglas activas hubo: es como D1 le cuenta al
    supervisor que el catalogo quedo vacio (T-18) sin que esta funcion lo conozca.
    """
    transaccion = Transaccion.model_validate(documento)
    reglas = await repo.leer_reglas_activas()
    if al_leer_reglas is not None:
        al_leer_reglas(len(reglas))
    if not reglas:
        # Pasa, por ejemplo, si se re-siembra 01-colecciones.js sin correr despues
        # 03-catalogos.js. Sin reglas ninguna transaccion alcanza el umbral, y eso no
        # puede pasar en silencio.
        registro_log.warning(
            "%s evaluada sin reglas activas: no puede generar alerta",
            transaccion.transaccion_id,
        )
    politica = await repo.leer_politica_vigente()
    origen = await repo.leer_origen(transaccion)
    contexto = await repo.armar_contexto(transaccion, origen, reglas)

    evaluacion = evaluar(transaccion, contexto, reglas, politica)
    for no_evaluada in evaluacion.reglas_no_evaluadas:
        registro_log.debug(
            "%s: %s no evaluada (%s)",
            transaccion.transaccion_id,
            no_evaluada.codigo,
            no_evaluada.motivo,
        )

    alerta = construir_alerta(transaccion, evaluacion, origen, ahora)
    if alerta is None:
        return Resultado.SIN_ALERTA
    if await repo.insertar_alerta(alerta):
        registro_log.info(
            "alerta %s para %s: puntaje %d %s",
            alerta.alerta_id,
            transaccion.transaccion_id,
            alerta.puntaje,
            alerta.severidad,
        )
        return Resultado.ALERTA_CREADA
    registro_log.info(
        "%s ya tenia alerta: evento reprocesado, el indice unico rechazo el duplicado",
        transaccion.transaccion_id,
    )
    return Resultado.YA_PROCESADA


async def procesar_lote(
    repo: ProtocoloRepoDeteccion,
    documentos: Sequence[Mapping[str, Any]],
    *,
    ahora: datetime,
    al_leer_reglas: Callable[[int], None] | None = None,
) -> list[Resultado | Exception]:
    """Contexto -> evaluar -> alertas de todo un lote. No toca el token.

    Devuelve un elemento por documento, en el mismo orden: el `Resultado`, o la excepcion
    de esa transaccion (no valida, sin cuenta de origen, un documento de contexto roto, un
    `_id` de alerta tomado). Quien llama aisla cada excepcion procesando ese evento solo.
    Un fallo que afecta a todo el lote (la politica no esta, la base no contesta) sube.

    El resultado de cada transaccion es el de `procesar_transaccion`: lo unico que cambia
    es cuantas veces se pregunta a Mongo. Las transacciones se evaluan en el orden del
    lote.
    """
    resultados: list[Resultado | Exception | None] = [None] * len(documentos)
    validas: list[tuple[int, Transaccion]] = []
    for indice, documento in enumerate(documentos):
        try:
            validas.append((indice, Transaccion.model_validate(documento)))
        except ValidationError as fallo:
            resultados[indice] = fallo

    reglas = await repo.leer_reglas_activas()
    if al_leer_reglas is not None:
        al_leer_reglas(len(reglas))
    if not reglas:
        registro_log.warning(
            "lote de %d transacciones evaluado sin reglas activas: no puede generar alerta",
            len(documentos),
        )
    politica = await repo.leer_politica_vigente()

    origenes = await repo.leer_origenes([transaccion for _, transaccion in validas])
    con_origen: list[tuple[int, Transaccion]] = []
    datos_origen: dict[str, DatosOrigen] = {}
    for indice, transaccion in validas:
        origen = origenes[transaccion.cuenta_origen]
        if isinstance(origen, Exception):
            resultados[indice] = origen
        else:
            datos_origen[transaccion.cuenta_origen] = origen
            con_origen.append((indice, transaccion))

    contextos = await repo.armar_contextos(
        [transaccion for _, transaccion in con_origen], datos_origen, reglas
    )
    alertas: list[tuple[int, Alerta]] = []
    for indice, transaccion in con_origen:
        contexto = contextos[transaccion.transaccion_id]
        if isinstance(contexto, Exception):
            resultados[indice] = contexto
            continue
        try:
            evaluacion = evaluar(transaccion, contexto, reglas, politica)
            for no_evaluada in evaluacion.reglas_no_evaluadas:
                registro_log.debug(
                    "%s: %s no evaluada (%s)",
                    transaccion.transaccion_id,
                    no_evaluada.codigo,
                    no_evaluada.motivo,
                )
            alerta = construir_alerta(
                transaccion, evaluacion, datos_origen[transaccion.cuenta_origen], ahora
            )
        except Exception as fallo:  # noqa: BLE001 - se aisla: ese evento se procesa solo
            resultados[indice] = fallo
            continue
        if alerta is None:
            resultados[indice] = Resultado.SIN_ALERTA
        else:
            alertas.append((indice, alerta))

    escritas = await repo.insertar_alertas([alerta for _, alerta in alertas])
    for indice, alerta in alertas:
        escrita = escritas[alerta.transaccion_id]
        if isinstance(escrita, Exception):
            resultados[indice] = escrita
        elif escrita:
            registro_log.info(
                "alerta %s para %s: puntaje %d %s",
                alerta.alerta_id,
                alerta.transaccion_id,
                alerta.puntaje,
                alerta.severidad,
            )
            resultados[indice] = Resultado.ALERTA_CREADA
        else:
            registro_log.info(
                "%s ya tenia alerta: evento reprocesado, el indice unico rechazo el duplicado",
                alerta.transaccion_id,
            )
            resultados[indice] = Resultado.YA_PROCESADA
    completos = [resultado for resultado in resultados if resultado is not None]
    assert len(completos) == len(documentos), "cada documento tiene un resultado"
    return completos


def latencia_ms(insertada_en: datetime | None, escrita_en: datetime) -> float | None:
    """Milisegundos entre la insercion y la escritura de la alerta, o `None` sin hora."""
    if insertada_en is None:
        return None
    return (escrita_en - insertada_en).total_seconds() * 1000


def atraso_segundos(insertada_en: datetime, ahora: datetime) -> float:
    """Segundos entre la insercion del evento y `ahora`; un desfase negativo cuenta como 0."""
    return max(0.0, round((ahora - insertada_en).total_seconds(), 1))


async def _procesar_con_reintentos(
    repo: ProtocoloRepoDeteccion,
    supervisor: Supervisor,
    documento: Mapping[str, Any],
    *,
    ajustes: AjustesDeteccion,
    nombre: str,
    reloj: Reloj,
) -> Resultado | None:
    """Procesa con espera progresiva; agotados los intentos, descarta y devuelve None.

    Un error de validacion (la transaccion o un documento de contexto no cumplen el
    modelo) no se arregla reintentando: va directo al descarte.
    """
    intentos = 0
    while True:
        intentos += 1
        try:
            return await procesar_transaccion(
                repo,
                documento,
                ahora=reloj(),
                al_leer_reglas=lambda activas: vigilar_catalogo(supervisor, nombre, activas),
            )
        except asyncio.CancelledError:
            raise
        except ValidationError as fallo:
            ultimo: Exception = fallo
            break
        except Exception as fallo:  # noqa: BLE001 - se reintenta y, si no, se descarta
            ultimo = fallo
            if intentos > ajustes.reintentos:
                break
            registro_log.warning(
                "%s: intento %d fallo (%s); reintento", documento.get("_id"), intentos, fallo
            )
            await asyncio.sleep(espera_progresiva(intentos - 1, ajustes))

    motivo = f"{type(ultimo).__name__}: {ultimo}"
    # Si esta escritura falla, el error sube: el token no avanza y el evento se
    # reprocesa al reabrir. Nada se pierde en silencio.
    await repo.descartar(
        nombre,
        transaccion_id=str(documento.get("_id")),
        motivo=motivo,
        intentos=intentos,
    )
    supervisor.anotar_error(nombre, ultimo)
    registro_log.error(
        "%s descartada tras %d intentos: %s", documento.get("_id"), intentos, motivo
    )
    return None


# ---------------------------------------------------------------------------
# El bucle
# ---------------------------------------------------------------------------


_FIN = object()


async def _lotes(
    flujo: AsyncIterator[EventoTransaccion], maximo: int
) -> AsyncIterator[list[EventoTransaccion]]:
    """Agrupa las vueltas del change stream: lo que haya, hasta `maximo`, sin esperar.

    Una tarea lee el cursor y deja las vueltas en una cola acotada; aqui se toma la
    primera (esperando si no hay) y, de ahi, solo lo que ya esta en la cola. Con poco
    trafico un evento sale solo, de inmediato; con una rafaga, el lote sale lleno. La
    lectura por adelantado no adelanta el token: ese lo guarda D1 al terminar cada lote.

    Una vuelta vacia (el cursor espero y no habia nada) es un grupo aparte, de un
    elemento sin documento. Un error del cursor se entrega DESPUES de los eventos que
    llegaron antes, como pasaba cuando el cursor se leia en el mismo bucle.
    """
    cola: asyncio.Queue[Any] = asyncio.Queue(maxsize=maximo)

    async def leer() -> None:
        try:
            async for vuelta in flujo:
                await cola.put(vuelta)
            await cola.put(_FIN)
        except asyncio.CancelledError:
            raise
        except Exception as fallo:  # noqa: BLE001 - se entrega a quien consume
            await cola.put(fallo)

    tarea = asyncio.create_task(leer())
    try:
        pendiente: Any = None
        while True:
            elemento = pendiente if pendiente is not None else await cola.get()
            pendiente = None
            if elemento is _FIN:
                return
            if isinstance(elemento, Exception):
                raise elemento
            if elemento.documento is None:
                yield [elemento]
                continue
            lote = [elemento]
            while len(lote) < maximo:
                try:
                    siguiente = cola.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if (
                    siguiente is _FIN
                    or isinstance(siguiente, Exception)
                    or siguiente.documento is None
                ):
                    pendiente = siguiente
                    break
                lote.append(siguiente)
            yield lote
    finally:
        # Primero se detiene la lectura: cerrar el generador del cursor con una lectura en
        # curso falla ("already running").
        tarea.cancel()
        with suppress(asyncio.CancelledError):
            await tarea


async def _procesar_grupo(
    repo: ProtocoloRepoDeteccion,
    supervisor: Supervisor,
    documentos: Sequence[Mapping[str, Any]],
    *,
    ajustes: AjustesDeteccion,
    nombre: str,
    reloj: Reloj,
) -> list[Resultado | None]:
    """Procesa los eventos de un lote y devuelve el resultado de cada uno (`None` = descarte).

    Un evento solo va por el camino de siempre. Dos o mas van por `procesar_lote`; lo que
    ese camino no resuelve (un evento con error propio, o un fallo de todo el lote) se
    procesa evento por evento con `_procesar_con_reintentos`: reintentos con espera
    progresiva y, agotados, descarte. Un evento envenenado no detiene ni descarta a los
    otros. Las alertas que el lote ya escribio no se repiten: el indice unico las cuenta
    como ya procesadas.
    """
    def al_leer_reglas(activas: int) -> None:
        vigilar_catalogo(supervisor, nombre, activas)

    if len(documentos) == 1:
        return [
            await _procesar_con_reintentos(
                repo, supervisor, documentos[0], ajustes=ajustes, nombre=nombre, reloj=reloj
            )
        ]
    resultados: list[Resultado | Exception | None]
    try:
        resultados = list(
            await procesar_lote(
                repo, documentos, ahora=reloj(), al_leer_reglas=al_leer_reglas
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as fallo:  # noqa: BLE001 - se aisla: cada evento se procesa solo
        registro_log.warning(
            "%s: el lote de %d eventos fallo (%s); se procesa evento por evento",
            nombre,
            len(documentos),
            fallo,
        )
        resultados = [fallo] * len(documentos)
    finales: list[Resultado | None] = []
    for documento, resultado in zip(documentos, resultados, strict=True):
        if isinstance(resultado, Exception):
            resultado = await _procesar_con_reintentos(
                repo, supervisor, documento, ajustes=ajustes, nombre=nombre, reloj=reloj
            )
        finales.append(resultado)
    return finales


@dataclass
class _Vigilancia:
    """Fallos seguidos de apertura: la base de la espera progresiva al reabrir."""

    fallos_seguidos: int = 0
    # Reloj monotono de la ultima medicion de rezago; `None` = todavia no se midio.
    rezago_medido_en: float | None = None
    # Atraso por tiempo (segundos) del ultimo evento procesado, o 0 si la ultima vuelta
    # llego vacia; `None` hasta la primera vuelta. No depende del orden de los `_id`.
    atraso_segundos: float | None = None


async def _medir_rezago(
    repo: ProtocoloRepoDeteccion,
    supervisor: Supervisor,
    *,
    nombre: str,
    ajustes: AjustesDeteccion,
    reloj: Reloj,
    vigilancia: _Vigilancia,
) -> None:
    """Mide el rezago (T-57) como mucho cada `rezago_intervalo_segundos`.

    Se llama despues de guardar el token, asi que la cifra incluye todo lo ya
    terminado. Una falla de la medicion no es una falla de D1: se registra y se sigue,
    la cifra anterior queda con su `medido_en` viejo.
    """
    ahora_monotono = time.monotonic()
    anterior = vigilancia.rezago_medido_en
    if anterior is not None and ahora_monotono - anterior < ajustes.rezago_intervalo_segundos:
        return
    vigilancia.rezago_medido_en = ahora_monotono
    try:
        medido = await repo.medir_rezago(nombre)
    except asyncio.CancelledError:
        raise
    except Exception as fallo:  # noqa: BLE001 - medir no puede detener la deteccion
        registro_log.warning("%s: no se pudo medir el rezago (%s)", nombre, fallo)
        return
    ahora = reloj()
    antiguedad = (
        max(0.0, round((ahora - medido.mas_vieja).total_seconds(), 1))
        if medido.mas_vieja is not None
        else None
    )
    supervisor.registrar_rezago(
        nombre,
        medido.pendientes,
        antiguedad,
        ahora,
        atraso_segundos=vigilancia.atraso_segundos,
    )


async def _consumir(
    repo: ProtocoloRepoDeteccion,
    supervisor: Supervisor,
    desde_token: Mapping[str, Any] | None,
    *,
    ajustes: AjustesDeteccion,
    nombre: str,
    reloj: Reloj,
    vigilancia: _Vigilancia,
) -> None:
    """Una apertura del change stream, consumida hasta que se cierre o falle."""
    hay_token_guardado = desde_token is not None
    flujo = repo.observar_transacciones(
        desde_token=desde_token, espera_ms=ajustes.espera_cursor_ms
    )
    async with aclosing(flujo) as vueltas, aclosing(_lotes(vueltas, ajustes.lote_maximo)) as lotes:
        async for lote in lotes:
            if lote[0].documento is not None:
                resultados = await _procesar_grupo(
                    repo,
                    supervisor,
                    [vuelta.documento for vuelta in lote if vuelta.documento is not None],
                    ajustes=ajustes,
                    nombre=nombre,
                    reloj=reloj,
                )
                # El orden del contrato: las alertas (o el descarte) del lote ya estan
                # escritas. Recien ahora avanza el token, una vez, al del ultimo evento.
                escrita_en = reloj()
                for vuelta, resultado in zip(lote, resultados, strict=True):
                    if resultado is Resultado.ALERTA_CREADA:
                        medida = latencia_ms(vuelta.insertada_en, escrita_en)
                        if medida is not None:
                            supervisor.registrar_latencia(nombre, medida)
                ultima = lote[-1]
                assert ultima.documento is not None
                transaccion_id = str(ultima.documento.get("_id"))
                await repo.guardar_token(nombre, ultima.token, transaccion_id=transaccion_id)
                hay_token_guardado = True
                for vuelta, resultado in zip(lote, resultados, strict=True):
                    supervisor.contar_evaluada(nombre)
                    supervisor.latir(nombre)
                    registro_log.debug(
                        "%s: %s", vuelta.documento.get("_id") if vuelta.documento else "?",
                        resultado or "descartada",
                    )
                if ultima.insertada_en is not None:
                    vigilancia.atraso_segundos = atraso_segundos(ultima.insertada_en, reloj())
            else:
                vuelta = lote[0]
                if not hay_token_guardado and vuelta.token is not None:
                    # Arranque sin token: se guarda el de la primera vuelta vacia, para
                    # que una caida antes de la primera transaccion no reinicie "desde
                    # ahora" y pierda lo que entre mientras D1 esta abajo. Es seguro: una
                    # vuelta vacia no tiene escrituras pendientes.
                    await repo.guardar_token(nombre, vuelta.token, transaccion_id=None)
                    hay_token_guardado = True
                # La vuelta llego vacia: el cursor espero y no habia nada. D1 esta al dia.
                vigilancia.atraso_segundos = 0.0
                supervisor.latir(nombre)
            vigilancia.fallos_seguidos = 0
            await _medir_rezago(
                repo,
                supervisor,
                nombre=nombre,
                ajustes=ajustes,
                reloj=reloj,
                vigilancia=vigilancia,
            )


async def d1(
    repo: ProtocoloRepoDeteccion,
    supervisor: Supervisor,
    *,
    ajustes: AjustesDeteccion,
    nombre: str = NOMBRE_D1,
    reloj: Reloj = _ahora,
) -> None:
    """Bucle infinito de D1. Lo lanza el lifespan: `supervisor.lanzar(NOMBRE_D1, d1(...))`.

    Recibe el repositorio y no la base: D1 no conoce el driver, y las pruebas le pasan
    un doble. `asyncio.CancelledError` se deja pasar siempre; atraparla convierte el
    apagado del lifespan en una tarea que no muere.
    """
    vigilancia = _Vigilancia()
    supervisor.habilitar_latencia(nombre)
    while True:
        try:
            # T-18: el catalogo se mira tambien al abrir, para que `/estado` diga
            # "sin reglas" desde el arranque y no recien con la primera transaccion.
            vigilar_catalogo(supervisor, nombre, len(await repo.leer_reglas_activas()))
            token = await repo.leer_token(nombre)
            if token is None:
                registro_log.info("%s: sin token guardado, arranca desde ahora", nombre)
            try:
                await _consumir(
                    repo,
                    supervisor,
                    token,
                    ajustes=ajustes,
                    nombre=nombre,
                    reloj=reloj,
                    vigilancia=vigilancia,
                )
            except TokenDeReanudacionInvalido as fallo:
                registro_log.warning(
                    "%s: resincronizar. %s. Arranca desde ahora: las transacciones "
                    "insertadas entre el token y este momento NO se evaluan",
                    nombre,
                    fallo,
                )
                supervisor.anotar_error(nombre, fallo)
                await _consumir(
                    repo,
                    supervisor,
                    None,
                    ajustes=ajustes,
                    nombre=nombre,
                    reloj=reloj,
                    vigilancia=vigilancia,
                )
            # El cursor murio sin error. Se reabre desde el ultimo token, con la espera
            # minima para que un cursor que muere en cada apertura no gire en vacio.
            registro_log.warning("%s: el change stream se cerro; se reabre", nombre)
            await asyncio.sleep(ajustes.espera_inicial_segundos)
        except asyncio.CancelledError:
            raise
        except Exception as fallo:  # noqa: BLE001 - D1 nunca tumba el proceso
            espera = espera_progresiva(vigilancia.fallos_seguidos, ajustes)
            vigilancia.fallos_seguidos += 1
            supervisor.anotar_error(nombre, fallo)
            registro_log.error(
                "%s: el change stream fallo (%s); se reabre desde el ultimo token en %.1f s",
                nombre,
                fallo,
                espera,
            )
            await asyncio.sleep(espera)
