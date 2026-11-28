"""H-11 · Disparador D2: alerta CRITICA -> notificacion -> correo.

Escucha `alertas` por change stream (solo inserts de severidad CRITICA, filtrados en el
servidor; ver `app/repos/notificaciones.py`) y, por cada alerta:

    notificacion `pendiente` -> envio SMTP (con reintentos) -> resultado -> token

El contrato es el de D1, y por que cada pieza:

- **Primero la notificacion, despues el token.** El token se guarda solo cuando las
  escrituras del evento terminaron (la notificacion en su estado final). Si el proceso
  muere en medio, al reanudar se reprocesa el evento.
- **La no duplicacion la impone la base.** `notificaciones` tiene `_id` derivado de la
  alerta e indice unico `(alerta_id, canal)`: reprocesar choca y no crea otra. Segun el
  estado que dejo la corrida anterior, el reproceso no hace nada (`enviado` o `fallido`)
  o retoma los intentos que faltaban (`pendiente`).
- **Entrega al menos una vez, y se dice asi.** Si D2 muere despues de que el servidor SMTP
  acepto el correo y antes de anotar `enviado`, al reanudar lo envia de nuevo. No se
  promete exactamente una vez; el `Message-ID` fijo por alerta permite reconocer el
  repetido.
- **Un correo que falla no detiene a D2 ni se reintenta sin fin.** Cada alerta tiene
  `notificaciones.reintentos_envio` intentos con espera progresiva; agotados queda
  `fallido` con el motivo en `detalle_error`, el fallo se anota en `/estado` y D2 sigue
  con la alerta siguiente.
- **Lo que no es un fallo de correo** (la base no contesta, un documento de alerta que no
  cumple el modelo) se reintenta con la misma espera y, agotado, va al descarte
  (`deteccion_estado`, clase `descarte`). Si ni el descarte se puede escribir, el error
  sube, el token no avanza y D2 reabre desde el ultimo token guardado.
- **Token inservible** (`ChangeStreamHistoryLost`, un token corrupto, o un `invalidate`
  porque alguien borro `alertas`): se registra como `resincronizar` y D2 arranca desde
  ahora. Lo que entro entre el token y ahora NO se notifica, y queda en el registro y en
  `/estado` como ultimo error.

Aviso al cliente (T-82): tras el correo, la misma vuelta avisa al CLIENTE por SMS simulado
(`app/deteccion/aviso_cliente.py`) si su ficha trae telefono y consentimiento. Va dentro del
mismo evento: el token solo avanza cuando el correo y el SMS terminaron, y reprocesar no
duplica ninguno de los dos (cada uno tiene su propia clave de idempotencia).

D2 procesa evento por evento, sin lotes: las alertas CRITICAS son pocas y cada una
espera un envio por red, asi que un lote no ahorraria nada.

Este modulo no importa `app/api/`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import aclosing
from datetime import UTC, datetime
from email.message import EmailMessage
from enum import StrEnum
from functools import partial
from typing import Any

from pydantic import ValidationError

from app.deteccion.aviso_cliente import AvisoCliente
from app.deteccion.correo import armar_correo, enviar_por_smtp, notificacion_id_para
from app.modelos import ESQUEMA_VERSION, Alerta
from app.nucleo.config import AjustesNotificaciones
from app.repos.flujo_alertas import TokenDeReanudacionInvalido
from app.repos.notificaciones import ESTADOS_TERMINALES, ProtocoloRepoNotificaciones
from app.supervision import Supervisor

registro_log = logging.getLogger("centinela.d2")

# Clave de `DISPARADORES_PREVISTOS` y `_id` del token en `deteccion_estado`.
NOMBRE_D2 = "d2_notificacion"
SEVERIDAD_NOTIFICABLE = "CRITICA"
CANAL_CORREO = "correo"

Reloj = Callable[[], datetime]
# Envia un mensaje o lanza. En produccion corre `smtplib` en un hilo; las pruebas pasan otro.
Enviador = Callable[[EmailMessage], Awaitable[None]]
Espera = Callable[[float], Awaitable[None]]


def _ahora() -> datetime:
    return datetime.now(UTC)


class Resultado(StrEnum):
    """Que paso con una alerta. Es lo que D2 registra por evento."""

    ENVIADA = "enviada"
    FALLIDA = "fallida"
    YA_PROCESADA = "ya_procesada"
    IGNORADA = "ignorada"


# ---------------------------------------------------------------------------
# Partes puras
# ---------------------------------------------------------------------------


def es_notificable(documento: Mapping[str, Any]) -> bool:
    """El filtro de severidad. El stream ya filtra; esto es el cinturon y los tirantes."""
    return documento.get("severidad") == SEVERIDAD_NOTIFICABLE


def espera_progresiva(intento: int, ajustes: AjustesNotificaciones) -> float:
    """Segundos a esperar antes del intento siguiente: se duplica hasta el tope."""
    return min(ajustes.espera_inicial_segundos * (2**intento), ajustes.espera_maxima_segundos)


def documento_pendiente(
    alerta: Alerta, mensaje: EmailMessage, ajustes: AjustesNotificaciones, ahora: datetime
) -> dict[str, Any]:
    """La notificacion recien creada, lista para insertar. Cumple el validador de Mongo."""
    return {
        "_id": notificacion_id_para(alerta.alerta_id),
        "alerta_id": alerta.alerta_id,
        "canal": CANAL_CORREO,
        "estado_envio": "pendiente",
        "fecha": ahora,
        "intentos": 0,
        "asunto": str(mensaje["Subject"]),
        "destinatarios": list(ajustes.destinatarios),
        "esquema_version": ESQUEMA_VERSION,
    }


def mensaje_de_error(fallo: BaseException) -> str:
    return f"{type(fallo).__name__}: {fallo}" if str(fallo) else type(fallo).__name__


def enviador_smtp(ajustes: AjustesNotificaciones) -> Enviador:
    """El enviador de verdad: `smtplib` en un hilo, para no bloquear el bucle de eventos."""
    enviar = partial(
        enviar_por_smtp,
        host=ajustes.smtp_host,
        puerto=ajustes.smtp_puerto,
        espera_segundos=ajustes.espera_smtp_segundos,
    )

    async def _enviar(mensaje: EmailMessage) -> None:
        await asyncio.to_thread(enviar, mensaje)

    return _enviar


# ---------------------------------------------------------------------------
# Un evento
# ---------------------------------------------------------------------------


async def procesar_alerta(
    repo: ProtocoloRepoNotificaciones,
    documento: Mapping[str, Any],
    *,
    ajustes: AjustesNotificaciones,
    enviar: Enviador,
    reloj: Reloj = _ahora,
    esperar: Espera = asyncio.sleep,
    al_fallar_envio: Callable[[BaseException], None] | None = None,
) -> Resultado:
    """Notificacion -> envio -> resultado. No toca el token: eso es de quien llama.

    Un fallo de ENVIO no sube: queda en la notificacion (`pendiente` entre intentos,
    `fallido` al agotarlos) y devuelve `FALLIDA`. Lo que si sube es un fallo de la base o
    una alerta que no cumple el modelo: es de quien llama (reintentos y descarte).
    """
    if not es_notificable(documento):
        return Resultado.IGNORADA
    alerta = Alerta.model_validate(documento)
    mensaje = armar_correo(alerta, ajustes)
    identificador = notificacion_id_para(alerta.alerta_id)

    creada = await repo.crear_pendiente(documento_pendiente(alerta, mensaje, ajustes, reloj()))
    intentos_hechos = 0
    if not creada:
        existente = await repo.leer_notificacion(identificador)
        if existente is None:  # borrada entre las dos lecturas: se vuelve a crear en el reproceso
            raise RuntimeError(f"{identificador} existia y ya no esta; se reprocesa")
        if existente.get("estado_envio") in ESTADOS_TERMINALES:
            registro_log.info(
                "%s ya tenia notificacion %s: evento reprocesado, no se envia otra vez",
                alerta.alerta_id,
                existente.get("estado_envio"),
            )
            return Resultado.YA_PROCESADA
        # `pendiente`: D2 murio entre intentos. Se retoma lo que faltaba, sin empezar de cero.
        intentos_hechos = int(existente.get("intentos", 0))
        registro_log.warning(
            "%s: notificacion pendiente con %d intentos; se retoma", alerta.alerta_id, intentos_hechos
        )

    ultimo_error = "reintentos agotados antes de reanudar"
    agotada_al_retomar = intentos_hechos >= ajustes.reintentos_envio
    while intentos_hechos < ajustes.reintentos_envio:
        intentos_hechos += 1
        try:
            await enviar(mensaje)
        except asyncio.CancelledError:
            raise
        except Exception as fallo:  # noqa: BLE001 - un correo que falla no detiene a D2
            ultimo_error = mensaje_de_error(fallo)
            if al_fallar_envio is not None:
                al_fallar_envio(fallo)
            ultimo = intentos_hechos >= ajustes.reintentos_envio
            registro_log.warning(
                "%s: intento %d de %d de enviar el correo fallo (%s)",
                alerta.alerta_id,
                intentos_hechos,
                ajustes.reintentos_envio,
                ultimo_error,
            )
            await repo.registrar_intento(
                identificador,
                estado_envio="fallido" if ultimo else "pendiente",
                intentos=intentos_hechos,
                fecha=reloj(),
                detalle_error=ultimo_error,
            )
            if not ultimo:
                await esperar(espera_progresiva(intentos_hechos - 1, ajustes))
            continue
        await repo.registrar_intento(
            identificador,
            estado_envio="enviado",
            intentos=intentos_hechos,
            fecha=reloj(),
            detalle_error=None,
        )
        registro_log.info(
            "%s: correo enviado (%s), intento %d", alerta.alerta_id, identificador, intentos_hechos
        )
        return Resultado.ENVIADA

    if agotada_al_retomar:
        # Se retomo una pendiente que ya habia agotado el tope (D2 murio justo antes de
        # cerrarla): no se reenvia, se cierra como fallida.
        await repo.registrar_intento(
            identificador,
            estado_envio="fallido",
            intentos=intentos_hechos,
            fecha=reloj(),
            detalle_error=ultimo_error,
        )
    return Resultado.FALLIDA


async def _procesar_con_reintentos(
    repo: ProtocoloRepoNotificaciones,
    supervisor: Supervisor,
    documento: Mapping[str, Any],
    *,
    ajustes: AjustesNotificaciones,
    enviar: Enviador,
    nombre: str,
    reloj: Reloj,
    esperar: Espera,
    aviso: AvisoCliente | None = None,
) -> Resultado | None:
    """Procesa con espera progresiva ante fallos de la base; agotados, descarta (`None`).

    Un error de validacion (el documento no cumple el modelo de alerta) no se arregla
    reintentando: va directo al descarte.
    """
    intentos = 0
    while True:
        intentos += 1
        try:
            resultado = await procesar_alerta(
                repo,
                documento,
                ajustes=ajustes,
                enviar=enviar,
                reloj=reloj,
                esperar=esperar,
                al_fallar_envio=lambda fallo: supervisor.anotar_error(nombre, fallo),
            )
            if aviso is not None and resultado is not Resultado.IGNORADA:
                # Idempotente: un reintento por una falla de la base no manda otro SMS.
                await aviso.procesar(documento)
            return resultado
        except asyncio.CancelledError:
            raise
        except ValidationError as fallo:
            ultimo: Exception = fallo
            break
        except Exception as fallo:  # noqa: BLE001 - se reintenta y, si no, se descarta
            ultimo = fallo
            if intentos > ajustes.reintentos_envio:
                break
            registro_log.warning(
                "%s: intento %d fallo (%s); reintento", documento.get("_id"), intentos, fallo
            )
            await esperar(espera_progresiva(intentos - 1, ajustes))
    motivo = mensaje_de_error(ultimo)
    # Si esta escritura falla, el error sube: el token no avanza y el evento se reprocesa
    # al reabrir. Nada se pierde en silencio.
    await repo.descartar(
        nombre,
        transaccion_id=str(documento.get("transaccion_id")),
        motivo=motivo,
        intentos=intentos,
    )
    supervisor.anotar_error(nombre, ultimo)
    registro_log.error("%s descartada tras %d intentos: %s", documento.get("_id"), intentos, motivo)
    return None


# ---------------------------------------------------------------------------
# El bucle
# ---------------------------------------------------------------------------


class _Vigilancia:
    """Estado entre aperturas: fallos seguidos y cuando se guardo el ultimo token."""

    def __init__(self) -> None:
        self.fallos_seguidos = 0
        # Reloj monotono del ultimo token guardado; `None` = todavia no hay.
        self.token_guardado_en: float | None = None


async def _consumir(
    repo: ProtocoloRepoNotificaciones,
    supervisor: Supervisor,
    desde_token: Mapping[str, Any] | None,
    *,
    ajustes: AjustesNotificaciones,
    enviar: Enviador,
    nombre: str,
    reloj: Reloj,
    esperar: Espera,
    vigilancia: _Vigilancia,
    aviso: AvisoCliente | None = None,
) -> None:
    """Una apertura del change stream, consumida hasta que se cierre o falle."""
    if desde_token is not None:
        vigilancia.token_guardado_en = vigilancia.token_guardado_en or time.monotonic()
    flujo = repo.observar_alertas_criticas(
        desde_token=desde_token, espera_ms=ajustes.espera_cursor_ms
    )
    async with aclosing(flujo) as vueltas:
        async for vuelta in vueltas:
            if vuelta.documento is not None:
                resultado = await _procesar_con_reintentos(
                    repo,
                    supervisor,
                    vuelta.documento,
                    ajustes=ajustes,
                    enviar=enviar,
                    nombre=nombre,
                    reloj=reloj,
                    esperar=esperar,
                    aviso=aviso,
                )
                # El orden del contrato: la notificacion (o el descarte) ya esta escrita.
                # Recien ahora avanza el token.
                assert vuelta.token is not None
                await repo.guardar_token(
                    nombre,
                    vuelta.token,
                    transaccion_id=str(vuelta.documento.get("transaccion_id")),
                )
                vigilancia.token_guardado_en = time.monotonic()
                supervisor.contar_evaluada(nombre)
                supervisor.latir(nombre)
                registro_log.debug("%s: %s", vuelta.documento.get("_id"), resultado or "descartada")
            else:
                # La vuelta llego vacia: el cursor espero y no habia nada. Es seguro
                # guardar su token (no hay escrituras pendientes); se hace al arrancar sin
                # token y de vez en cuando, para que el token no envejezca mas alla del
                # oplog mientras el stream filtrado no ve eventos.
                if vuelta.token is not None and _toca_refrescar(vigilancia, ajustes):
                    await repo.guardar_token(nombre, vuelta.token, transaccion_id=None)
                    vigilancia.token_guardado_en = time.monotonic()
                supervisor.latir(nombre)
            vigilancia.fallos_seguidos = 0


def _toca_refrescar(vigilancia: _Vigilancia, ajustes: AjustesNotificaciones) -> bool:
    if vigilancia.token_guardado_en is None:
        return True
    return time.monotonic() - vigilancia.token_guardado_en >= ajustes.refresco_reanudacion_segundos


async def d2(
    repo: ProtocoloRepoNotificaciones,
    supervisor: Supervisor,
    *,
    ajustes: AjustesNotificaciones,
    enviar: Enviador | None = None,
    nombre: str = NOMBRE_D2,
    reloj: Reloj = _ahora,
    esperar: Espera = asyncio.sleep,
    aviso: AvisoCliente | None = None,
) -> None:
    """Bucle infinito de D2. Lo lanza el lifespan: `supervisor.lanzar(NOMBRE_D2, d2(...))`.

    Recibe el repositorio y no la base: D2 no conoce el driver, y las pruebas le pasan un
    doble. `enviar` por omision es SMTP en un hilo; las pruebas pasan uno propio. `aviso` es el
    paso que avisa al cliente por SMS (T-82); sin el, D2 solo manda el correo.
    `asyncio.CancelledError` se deja pasar siempre.
    """
    enviar = enviar or enviador_smtp(ajustes)
    vigilancia = _Vigilancia()
    while True:
        try:
            token = await repo.leer_token(nombre)
            if token is None:
                registro_log.info("%s: sin token guardado, arranca desde ahora", nombre)
            try:
                await _consumir(
                    repo,
                    supervisor,
                    token,
                    ajustes=ajustes,
                    enviar=enviar,
                    nombre=nombre,
                    reloj=reloj,
                    esperar=esperar,
                    vigilancia=vigilancia,
                    aviso=aviso,
                )
            except TokenDeReanudacionInvalido as fallo:
                registro_log.warning(
                    "%s: resincronizar. %s. Arranca desde ahora: las alertas criticas "
                    "insertadas entre el token y este momento NO se notifican",
                    nombre,
                    fallo,
                )
                supervisor.anotar_error(nombre, fallo)
                vigilancia.token_guardado_en = None
                await _consumir(
                    repo,
                    supervisor,
                    None,
                    ajustes=ajustes,
                    enviar=enviar,
                    nombre=nombre,
                    reloj=reloj,
                    esperar=esperar,
                    vigilancia=vigilancia,
                    aviso=aviso,
                )
            # El cursor murio sin error. Se reabre desde el ultimo token, con la espera
            # minima para que un cursor que muere en cada apertura no gire en vacio.
            registro_log.warning("%s: el change stream se cerro; se reabre", nombre)
            await esperar(ajustes.espera_inicial_segundos)
        except asyncio.CancelledError:
            raise
        except Exception as fallo:  # noqa: BLE001 - D2 nunca tumba el proceso
            espera = espera_progresiva(vigilancia.fallos_seguidos, ajustes)
            vigilancia.fallos_seguidos += 1
            supervisor.anotar_error(nombre, fallo)
            registro_log.error(
                "%s: el change stream fallo (%s); se reabre desde el ultimo token en %.1f s",
                nombre,
                fallo,
                espera,
            )
            await esperar(espera)


__all__ = [
    "NOMBRE_D2",
    "Resultado",
    "d2",
    "documento_pendiente",
    "enviador_smtp",
    "es_notificable",
    "espera_progresiva",
    "procesar_alerta",
]
