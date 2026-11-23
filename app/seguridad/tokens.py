"""Token de sesion firmado con HMAC-SHA256 (biblioteca estandar).

Formato: `<carga>.<firma>`, las dos en base64 url sin relleno. La carga es JSON con
`sub` (agente_id), `rol`, `iat` y `exp` (segundos desde la epoca, UTC). Se firma con
una clave del entorno: quien no la tiene no puede fabricar ni alterar un token, y el
rol que viaja adentro no se puede subir de `agente` a `administrador` sin romper la firma.

El token es sin estado: no se consulta la base en cada peticion. A cambio, un agente
desactivado conserva su sesion hasta que expire; por eso la duracion es corta y se
configura en `sesion.minutos`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.modelos.alertas import RolAgente


class TokenInvalido(Exception):
    """El token no sirve. `motivo` es para el registro, nunca para el cliente."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True)
class Sesion:
    agente_id: str
    rol: RolAgente
    expira: datetime


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def _desde_b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _firmar(clave: bytes, carga: str) -> str:
    return _b64(hmac.new(clave, carga.encode("ascii"), hashlib.sha256).digest())


def emitir(
    clave: bytes,
    agente_id: str,
    rol: RolAgente,
    *,
    minutos: int,
    ahora: datetime | None = None,
) -> tuple[str, datetime]:
    """Token firmado y el instante en que expira."""
    emision = (ahora or datetime.now(UTC)).replace(microsecond=0)
    expira = emision + timedelta(minutes=minutos)
    carga = _b64(
        json.dumps(
            {
                "sub": agente_id,
                "rol": RolAgente(rol).value,
                "iat": int(emision.timestamp()),
                "exp": int(expira.timestamp()),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return f"{carga}.{_firmar(clave, carga)}", expira


def verificar(clave: bytes, token: str, *, ahora: datetime | None = None) -> Sesion:
    """La sesion que el token acredita, o `TokenInvalido`."""
    partes = token.split(".")
    if len(partes) != 2 or not all(partes):
        raise TokenInvalido("formato")
    carga, firma = partes
    try:
        firma_esperada = _firmar(clave, carga)
    except UnicodeEncodeError as fallo:
        raise TokenInvalido("formato") from fallo
    # La firma se comprueba ANTES de interpretar la carga: no se parsea nada que no
    # haya firmado este servidor.
    if not hmac.compare_digest(firma.encode("ascii", "replace"), firma_esperada.encode("ascii")):
        raise TokenInvalido("firma")
    try:
        datos = json.loads(_desde_b64(carga))
        expira = datetime.fromtimestamp(int(datos["exp"]), UTC)
        agente_id = datos["sub"]
        rol = RolAgente(datos["rol"])
    except (ValueError, KeyError, TypeError) as fallo:
        raise TokenInvalido("carga") from fallo
    if not isinstance(agente_id, str) or not agente_id:
        raise TokenInvalido("carga")
    if (ahora or datetime.now(UTC)) >= expira:
        raise TokenInvalido("expirado")
    return Sesion(agente_id=agente_id, rol=rol, expira=expira)
