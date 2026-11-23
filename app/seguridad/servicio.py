"""El servicio de sesion del proceso: la clave de firma, la duracion y la cookie.

Se crea una vez en el lifespan y se deja en `aplicacion.state`. Junta lo que `tokens.py`
necesita (la clave) con lo que viene de la configuracion, para que nadie mas toque la
clave.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime

from app.modelos.alertas import RolAgente
from app.nucleo.config import Ajustes
from app.seguridad import tokens
from app.seguridad.tokens import Sesion, TokenInvalido

ADVERTENCIA_CLAVE_EFIMERA = (
    "clave de sesion generada al arrancar (CENTINELA_SESION__CLAVE no esta definida): "
    "las sesiones se pierden al reiniciar. Solo se tolera en entorno dev."
)


@dataclass(frozen=True)
class ServicioSesion:
    clave: bytes = field(repr=False)
    minutos: int
    nombre_cookie: str
    cookie_segura: bool
    advertencias: tuple[str, ...] = ()

    @classmethod
    def desde_ajustes(cls, ajustes: Ajustes) -> ServicioSesion:
        configurada = ajustes.sesion.clave
        # Fuera de dev `Ajustes` ya se nego a existir sin clave; aqui solo se genera una.
        clave = configurada if configurada else secrets.token_urlsafe(48)
        return cls(
            clave=clave.encode("utf-8"),
            minutos=ajustes.sesion.minutos,
            nombre_cookie=ajustes.sesion.nombre_cookie,
            cookie_segura=ajustes.sesion.cookie_segura,
            advertencias=() if configurada else (ADVERTENCIA_CLAVE_EFIMERA,),
        )

    def emitir(self, agente_id: str, rol: RolAgente) -> tuple[str, datetime]:
        return tokens.emitir(self.clave, agente_id, rol, minutos=self.minutos)

    def verificar(self, token: str) -> Sesion:
        return tokens.verificar(self.clave, token)

    def leer(self, token: str | None) -> Sesion | None:
        """La sesion del token, o `None` si falta o no sirve (nunca lanza)."""
        if not token:
            return None
        try:
            return self.verificar(token)
        except TokenInvalido:
            return None
