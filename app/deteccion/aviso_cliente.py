"""T-82 · Aviso al cliente: SMS simulado con el enlace a su portal.

Lo corre D2 junto al correo del supervisor. Por cada alerta CRITICA cuyo cliente tenga
telefono y `acepta_sms`, deja una notificacion de canal `sms` con el texto del mensaje y un
enlace de un solo uso (`/c/{token}`). El cliente responde "Si, fui yo" o "No, no fui yo".

Decisiones, y por que:

- **Proveedor detras de una interfaz.** `EnviadorSms` es lo unico que sabe como entregar un
  SMS. Hoy hay una implementacion, `EnviadorSmsSimulado`, que NO sale a ninguna red: el
  mensaje queda guardado en la notificacion y lo muestra la bandeja de `/simulador-sms`. Un
  proveedor real (una pasarela de SMS) se agrega implementando la misma interfaz y
  eligiendolo con `aviso_cliente.proveedor`; no se implementa aqui.
- **El enlace en la base es solo un hash.** El token en claro existe un instante en memoria
  y en el mensaje. La notificacion guarda `enlace_hash` (sha256), nunca el token. El texto
  del mensaje SI lleva el enlace, y solo lo guarda el proveedor simulado (es la bandeja del
  demo); un proveedor real devuelve `None` y no deja el enlace en ninguna parte de la base.
- **Sin datos sensibles en el SMS.** Monto, las cuatro ultimas cifras de SU cuenta y el
  enlace. Ni nombre ni cuenta completa ni destino.
- **Sin consentimiento no se envia.** Falta el telefono o `acepta_sms` no es `true`: no se
  escribe notificacion y queda una linea en el log de auditoria.
- **Idempotencia, la misma de D2.** Una alerta produce a lo sumo una notificacion `sms`
  (`_id` derivado `NOT-000123-SMS` mas el indice unico `(alerta_id, canal)`). Reprocesar un
  evento no crea otra ni manda otro SMS. Si D2 murio con la notificacion `pendiente`, el
  token en claro se perdio con el proceso: se emite uno NUEVO (el hash se reemplaza) y se
  envia. Entrega al menos una vez, igual que el correo.
- **Auditoria.** Cada envio y cada omision van al log `centinela.auditoria`, sin el token.

Este modulo no importa `app/api/`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.deteccion.correo import colones
from app.modelos import ESQUEMA_VERSION, Alerta
from app.nucleo.config import AjustesAvisoCliente, AjustesNotificaciones
from app.nucleo.enmascarar import telefono as telefono_enmascarado
from app.nucleo.enmascarar import terminada_en
from app.seguridad.enlaces import hash_de, nuevo_token, vence_en

registro_log = logging.getLogger("centinela.d2")
registro_auditoria = logging.getLogger("centinela.auditoria")

CANAL_SMS = "sms"
SEVERIDAD_AVISABLE = "CRITICA"

Reloj = Callable[[], datetime]
Espera = Callable[[float], Awaitable[None]]


def _ahora() -> datetime:
    return datetime.now(UTC)


class ResultadoAviso(StrEnum):
    ENVIADO = "enviado"
    FALLIDO = "fallido"
    YA_PROCESADO = "ya_procesado"
    SIN_CONSENTIMIENTO = "sin_consentimiento"
    IGNORADO = "ignorado"


# ---------------------------------------------------------------------------
# Partes puras
# ---------------------------------------------------------------------------


def notificacion_sms_id_para(alerta_id: str) -> str:
    """`ALR-000123` -> `NOT-000123-SMS`. Derivado, para que reprocesar la misma alerta choque."""
    if not alerta_id.startswith("ALR-"):
        raise ValueError(f"identificador de alerta inesperado: {alerta_id}")
    return "NOT-" + alerta_id.removeprefix("ALR-") + "-SMS"


def enlace_del_cliente(token: str, portal_url: str) -> str:
    return f"{portal_url.rstrip('/')}/c/{token}"


def texto_sms(alerta: Alerta, enlace: str) -> str:
    """El SMS. Monto y cuatro ultimas cifras de la cuenta del cliente: nada mas que sea sensible."""
    return (
        f"Banco: detectamos una transferencia SINPE de {colones(alerta.monto_crc)} desde tu "
        f"cuenta terminada en {terminada_en(alerta.cuenta_origen)}. "
        f"¿Fuiste tú? Responde aquí: {enlace}"
    )


def puede_recibir_sms(contacto: Mapping[str, Any] | None) -> bool:
    """Hay telefono Y consentimiento explicito (`acepta_sms` es `true`; ausente o falso no)."""
    return (
        contacto is not None
        and bool(contacto.get("telefono"))
        and contacto.get("acepta_sms") is True
    )


def documento_pendiente(
    alerta: Alerta,
    *,
    telefono: str,
    enlace_hash: str,
    vence: datetime,
    ahora: datetime,
) -> dict[str, Any]:
    """La notificacion `sms` recien creada. El telefono va enmascarado; el token, solo su hash."""
    return {
        "_id": notificacion_sms_id_para(alerta.alerta_id),
        "alerta_id": alerta.alerta_id,
        "canal": CANAL_SMS,
        "estado_envio": "pendiente",
        "fecha": ahora,
        "intentos": 0,
        "destinatarios": [telefono_enmascarado(telefono)],
        "enlace_hash": enlace_hash,
        "enlace_vence": vence,
        "esquema_version": ESQUEMA_VERSION,
    }


# ---------------------------------------------------------------------------
# El proveedor
# ---------------------------------------------------------------------------


@runtime_checkable
class EnviadorSms(Protocol):
    """Entrega un SMS. Lanza si no pudo.

    Devuelve el texto que la notificacion debe guardar, o `None` si el proveedor entrego el
    mensaje y no hay nada que conservar (un proveedor real: el enlace no queda en la base).
    """

    async def enviar(self, telefono: str, texto: str) -> str | None: ...


class EnviadorSmsSimulado:
    """Sin red ni secretos: el mensaje "sale" a la bandeja de `/simulador-sms`.

    Solo devuelve el texto para que D2 lo guarde en la notificacion. Es el unico proveedor
    de hoy; sirve para la demo y las pruebas.

    Para un proveedor real: otra clase con `async def enviar(telefono, texto)` que llame a la
    pasarela y devuelva `None`, una opcion nueva en `aviso_cliente.proveedor` y su eleccion en
    `enviador_sms`. Las credenciales entrarian por variable de entorno, como las demas.
    """

    async def enviar(self, telefono: str, texto: str) -> str | None:
        return texto


def enviador_sms(ajustes: AjustesAvisoCliente) -> EnviadorSms:
    if ajustes.proveedor == "simulado":
        return EnviadorSmsSimulado()
    raise ValueError(f"proveedor de SMS desconocido: {ajustes.proveedor}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Lo que necesita de la base
# ---------------------------------------------------------------------------


@runtime_checkable
class ProtocoloRepoAviso(Protocol):
    """Lo que el aviso necesita de `app/repos/aviso_cliente.py`. El doble de prueba lo cumple."""

    async def contacto_de_alerta(self, transaccion_id: str) -> Mapping[str, Any] | None:
        """`{telefono, acepta_sms}` del titular de la cuenta de la transaccion, o `None`."""
        ...

    async def crear_pendiente(self, documento: Mapping[str, Any]) -> bool: ...

    async def leer_notificacion(self, notificacion_id: str) -> Mapping[str, Any] | None: ...

    async def rotar_enlace(
        self, notificacion_id: str, *, enlace_hash: str, enlace_vence: datetime
    ) -> None: ...

    async def registrar_intento(
        self,
        notificacion_id: str,
        *,
        estado_envio: str,
        intentos: int,
        fecha: datetime,
        detalle_error: str | None,
        texto: str | None,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Un evento
# ---------------------------------------------------------------------------


class AvisoCliente:
    """El paso de D2 que avisa al cliente. `procesar` es idempotente por alerta."""

    def __init__(
        self,
        repo: ProtocoloRepoAviso,
        *,
        ajustes: AjustesAvisoCliente,
        notificaciones: AjustesNotificaciones,
        enviador: EnviadorSms | None = None,
        reloj: Reloj = _ahora,
        esperar: Espera = asyncio.sleep,
        generar_token: Callable[[], str] = nuevo_token,
    ) -> None:
        self._repo = repo
        self._ajustes = ajustes
        self._notificaciones = notificaciones
        self._enviador = enviador or enviador_sms(ajustes)
        self._reloj = reloj
        self._esperar = esperar
        self._generar_token = generar_token

    async def procesar(self, documento: Mapping[str, Any]) -> ResultadoAviso:
        if documento.get("severidad") != SEVERIDAD_AVISABLE:
            return ResultadoAviso.IGNORADO
        alerta = Alerta.model_validate(documento)
        contacto = await self._repo.contacto_de_alerta(alerta.transaccion_id)
        if not puede_recibir_sms(contacto):
            motivo = "sin_telefono" if not (contacto or {}).get("telefono") else "sin_consentimiento"
            registro_auditoria.info(
                "aviso_cliente_omitido alerta=%s motivo=%s", alerta.alerta_id, motivo
            )
            return ResultadoAviso.SIN_CONSENTIMIENTO
        assert contacto is not None
        telefono = str(contacto["telefono"])

        ahora = self._reloj()
        token = self._generar_token()
        vence = vence_en(ahora, self._ajustes.vigencia_minutos)
        identificador = notificacion_sms_id_para(alerta.alerta_id)
        creada = await self._repo.crear_pendiente(
            documento_pendiente(
                alerta, telefono=telefono, enlace_hash=hash_de(token), vence=vence, ahora=ahora
            )
        )
        intentos_hechos = 0
        if not creada:
            existente = await self._repo.leer_notificacion(identificador)
            if existente is None:
                raise RuntimeError(f"{identificador} existia y ya no esta; se reprocesa")
            if existente.get("estado_envio") in ("enviado", "fallido"):
                registro_log.info(
                    "%s ya tenia aviso al cliente %s: reproceso, no se envia otro",
                    alerta.alerta_id,
                    existente.get("estado_envio"),
                )
                return ResultadoAviso.YA_PROCESADO
            # `pendiente`: D2 murio antes de cerrarlo. El token en claro se perdio con el
            # proceso: se emite uno nuevo y se reemplaza el hash.
            intentos_hechos = int(existente.get("intentos", 0))
            await self._repo.rotar_enlace(
                identificador, enlace_hash=hash_de(token), enlace_vence=vence
            )

        texto = texto_sms(alerta, enlace_del_cliente(token, self._notificaciones.portal_url))
        topes = self._notificaciones.reintentos_envio
        ultimo_error = "reintentos agotados antes de reanudar"
        while intentos_hechos < topes:
            intentos_hechos += 1
            try:
                guardado = await self._enviador.enviar(telefono, texto)
            except asyncio.CancelledError:
                raise
            except Exception as fallo:  # noqa: BLE001 - un SMS que falla no detiene a D2
                ultimo_error = f"{type(fallo).__name__}: {fallo}"
                ultimo = intentos_hechos >= topes
                registro_log.warning(
                    "%s: intento %d de %d de enviar el SMS fallo (%s)",
                    alerta.alerta_id,
                    intentos_hechos,
                    topes,
                    ultimo_error,
                )
                await self._repo.registrar_intento(
                    identificador,
                    estado_envio="fallido" if ultimo else "pendiente",
                    intentos=intentos_hechos,
                    fecha=self._reloj(),
                    detalle_error=ultimo_error,
                    texto=None,
                )
                if not ultimo:
                    await self._esperar(
                        min(
                            self._notificaciones.espera_inicial_segundos
                            * (2 ** (intentos_hechos - 1)),
                            self._notificaciones.espera_maxima_segundos,
                        )
                    )
                continue
            await self._repo.registrar_intento(
                identificador,
                estado_envio="enviado",
                intentos=intentos_hechos,
                fecha=self._reloj(),
                detalle_error=None,
                texto=guardado,
            )
            registro_auditoria.info(
                "aviso_cliente_enviado alerta=%s notificacion=%s telefono=%s vence=%s",
                alerta.alerta_id,
                identificador,
                telefono_enmascarado(telefono),
                vence.isoformat(),
            )
            return ResultadoAviso.ENVIADO

        await self._repo.registrar_intento(
            identificador,
            estado_envio="fallido",
            intentos=intentos_hechos,
            fecha=self._reloj(),
            detalle_error=ultimo_error,
            texto=None,
        )
        registro_auditoria.warning(
            "aviso_cliente_fallido alerta=%s notificacion=%s", alerta.alerta_id, identificador
        )
        return ResultadoAviso.FALLIDO
