"""El portal del cliente (T-82): logica pura y carga del enlace. Sin HTTP.

`estado_del_caso` y `armar_enlace` son funciones puras, probables sin base. `cargar` lee el
enlace y aplica la regla uniforme: un enlace inexistente, vencido sin responder o sin forma de
token dan LA MISMA respuesta (`EnlaceNoValido`), para que quien lo prueba no sepa cual fue.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.api.esquemas_cliente import (
    AyudaAlCliente,
    EnlaceDelCliente,
    EstadoDelCaso,
    MensajeSms,
    RespuestaDelCliente,
    TransferenciaDelCliente,
)
from app.modelos.base import asumir_utc
from app.nucleo.config import AjustesAvisoCliente
from app.nucleo.enmascarar import cuenta_destino, terminada_en
from app.repos.aviso_cliente import ProtocoloRepoRespuestaCliente
from app.seguridad.enlaces import (
    EstadoEnlace,
    estado_del_enlace,
    hash_de,
    tiene_forma_de_token,
)

ESTADOS_CERRADOS = ("confirmada", "descartada", "escalada")
_RUTA_DEL_ENLACE = re.compile(r"/c/[A-Za-z0-9_-]+")


class EnlaceNoValido(Exception):
    """No existe, vencio o es basura. Una sola excepcion para los tres casos, a proposito."""


class EnlaceYaRespondido(Exception):
    """Respondio antes (o el caso ya se cerro): no se acepta otra respuesta."""


def ayuda_de(ajustes: AjustesAvisoCliente) -> AyudaAlCliente:
    return AyudaAlCliente(
        telefono=ajustes.telefono_ayuda, es_ejemplo=ajustes.telefono_ayuda_es_ejemplo
    )


def estado_del_caso(
    estado_alerta: str, *, tiene_respuesta: bool, telefono_ayuda: str
) -> EstadoDelCaso | None:
    """El estado del caso en palabras simples, o `None` si no hay nada que contar todavia.

    Antes de responder no se muestra un estado (solo hay pregunta), salvo que el caso ya se
    haya cerrado por otro lado. Los textos son los del producto, en un solo lugar.
    """
    if estado_alerta == "confirmada":
        return EstadoDelCaso(
            codigo="fraude_confirmado",
            titulo=f"Confirmamos el fraude: llama al {telefono_ayuda}",
            detalle="Llama ahora para proteger tu cuenta. No compartas códigos ni claves con nadie.",
        )
    if estado_alerta == "descartada":
        return EstadoDelCaso(
            codigo="sin_fraude",
            titulo="Cerramos el caso: no hubo fraude",
            detalle="Revisamos la transferencia y todo está en orden. No tienes que hacer nada más.",
        )
    if estado_alerta == "escalada":
        return EstadoDelCaso(
            codigo="en_investigacion",
            titulo="Tu caso pasó a investigación",
            detalle=f"Un equipo especializado lo revisa. Si necesitas ayuda, llama al {telefono_ayuda}.",
        )
    if not tiene_respuesta:
        return None
    if estado_alerta == "en_revision":
        return EstadoDelCaso(
            codigo="en_revision",
            titulo="Un agente revisa tu caso",
            detalle="Esta página se actualiza sola cuando haya novedades.",
        )
    return EstadoDelCaso(
        codigo="recibida",
        titulo="Recibimos tu respuesta",
        detalle="Un agente la verá pronto. Esta página se actualiza sola.",
    )


def armar_enlace(
    *,
    alerta: dict[str, Any],
    notificacion: dict[str, Any],
    estado: EstadoEnlace,
    ajustes: AjustesAvisoCliente,
) -> EnlaceDelCliente:
    respuesta = alerta.get("respuesta_cliente") or notificacion.get("respuesta_cliente")
    cerrada = alerta["estado"] in ESTADOS_CERRADOS
    respondida = respuesta is not None
    return EnlaceDelCliente(
        fase="respondida" if respondida else "pendiente",
        puede_responder=estado is EstadoEnlace.VIGENTE and not respondida and not cerrada,
        transferencia=TransferenciaDelCliente(
            monto_crc=int(alerta["monto_crc"]),
            fecha=asumir_utc(alerta["fecha_creacion"]),
            cuenta_terminada_en=terminada_en(alerta["cuenta_origen"]),
            destino_enmascarado=cuenta_destino(alerta["cuenta_destino"]),
        ),
        vence=asumir_utc(notificacion["enlace_vence"]),
        respuesta=(
            RespuestaDelCliente(valor=respuesta["valor"], fecha=asumir_utc(respuesta["fecha"]))
            if respondida
            else None
        ),
        caso=estado_del_caso(
            alerta["estado"], tiene_respuesta=respondida, telefono_ayuda=ajustes.telefono_ayuda
        ),
        ayuda=ayuda_de(ajustes),
        sondeo_segundos=ajustes.sondeo_segundos,
    )


async def cargar(
    repo: ProtocoloRepoRespuestaCliente,
    token: str,
    *,
    ahora: datetime,
    ajustes: AjustesAvisoCliente,
) -> tuple[dict[str, Any], dict[str, Any], EstadoEnlace, str]:
    """`(notificacion, alerta, estado del enlace, hash)`. Lanza `EnlaceNoValido` si no hay enlace."""
    if not tiene_forma_de_token(token):
        raise EnlaceNoValido
    enlace_hash = hash_de(token)
    notificacion = await repo.notificacion_por_hash(enlace_hash)
    if notificacion is None:
        raise EnlaceNoValido
    estado = estado_del_enlace(
        ahora=ahora,
        vence=asumir_utc(notificacion["enlace_vence"]),
        usado_en=(
            asumir_utc(notificacion["enlace_usado_en"])
            if notificacion.get("enlace_usado_en") is not None
            else None
        ),
        seguimiento_horas=ajustes.seguimiento_horas,
    )
    if estado is EstadoEnlace.NO_VALIDO:
        raise EnlaceNoValido
    alerta = await repo.alerta_del_enlace(notificacion["alerta_id"])
    if alerta is None:
        raise EnlaceNoValido
    return notificacion, alerta, estado, enlace_hash


def mensaje_de_bandeja(documento: dict[str, Any], *, ahora: datetime) -> MensajeSms:
    texto = documento.get("texto")
    ruta = _RUTA_DEL_ENLACE.search(texto) if texto else None
    usado = documento.get("enlace_usado_en")
    vence = documento.get("enlace_vence")
    if usado is not None:
        enlace = "usado"
    elif vence is not None:
        enlace = "vigente" if ahora < asumir_utc(vence) else "vencido"
    else:
        enlace = None
    respuesta = documento.get("respuesta_cliente")
    return MensajeSms(
        notificacion_id=documento["_id"],
        alerta_id=documento["alerta_id"],
        para=(documento.get("destinatarios") or ["****-****"])[0],
        fecha=asumir_utc(documento["fecha"]),
        estado_envio=documento["estado_envio"],
        texto=texto,
        ruta_enlace=ruta.group(0) if ruta else None,
        enlace=enlace,  # type: ignore[arg-type]
        respuesta=(
            RespuestaDelCliente(valor=respuesta["valor"], fecha=asumir_utc(respuesta["fecha"]))
            if respuesta
            else None
        ),
    )


def ahora_utc() -> datetime:
    return datetime.now(UTC)

