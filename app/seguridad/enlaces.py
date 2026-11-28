"""El enlace de un solo uso del aviso al cliente (T-82): token, hash y vigencia.

Funciones puras, sin red ni base. El contrato de seguridad, y por que:

- **El token es aleatorio** (`secrets.token_urlsafe`, 256 bits): no se puede adivinar ni
  derivar de la alerta, y probar enlaces al azar no tiene salida practica.
- **En la base solo vive su hash** (sha256 en hexadecimal). Quien lea la coleccion
  `notificaciones` no puede reconstruir un enlace. Un hash simple y no uno lento (scrypt)
  porque el token ya tiene 256 bits de entropia: no hay nada que adivinar por fuerza bruta.
- **Vence** (`aviso_cliente.vigencia_minutos`) y **se invalida al responder**: el enlace
  deja responder una sola vez. Pasada la respuesta sigue sirviendo, solo de lectura, para
  ver el estado del caso durante `aviso_cliente.seguimiento_horas`.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from enum import StrEnum

BYTES_DEL_TOKEN = 32
# `token_urlsafe(32)` produce 43 caracteres de [A-Za-z0-9_-]. Se acepta un rango por si cambia.
_FORMA_DEL_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def nuevo_token() -> str:
    return secrets.token_urlsafe(BYTES_DEL_TOKEN)


def hash_de(token: str) -> str:
    """sha256 del token, en hexadecimal minuscula (64 caracteres): lo unico que se guarda."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tiene_forma_de_token(texto: str) -> bool:
    """Descarta basura antes de tocar la base: un texto que no puede ser un token no se busca."""
    return _FORMA_DEL_TOKEN.fullmatch(texto) is not None


def vence_en(ahora: datetime, vigencia_minutos: int) -> datetime:
    return ahora + timedelta(minutes=vigencia_minutos)


class EstadoEnlace(StrEnum):
    """Que puede hacer quien trae el enlace."""

    VIGENTE = "vigente"  # puede ver y responder
    RESPONDIDO = "respondido"  # ya respondio; solo lectura durante el seguimiento
    NO_VALIDO = "no_valido"  # vencido, o respondido hace mas que el seguimiento


def estado_del_enlace(
    *,
    ahora: datetime,
    vence: datetime,
    usado_en: datetime | None,
    seguimiento_horas: int,
) -> EstadoEnlace:
    """El estado de un enlace que SI existe. Un enlace inexistente es `NO_VALIDO` tambien.

    Vencido sin responder y desconocido dan el mismo resultado a proposito: quien trae un
    enlace no debe poder distinguir uno que nunca existio de uno que ya vencio.
    """
    if usado_en is not None:
        limite = usado_en + timedelta(hours=seguimiento_horas)
        return EstadoEnlace.RESPONDIDO if ahora < limite else EstadoEnlace.NO_VALIDO
    return EstadoEnlace.VIGENTE if ahora < vence else EstadoEnlace.NO_VALIDO
