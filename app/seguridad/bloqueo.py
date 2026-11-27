"""Limite de intentos de login (T-83): la cuenta que se bloquea y la IP que se frena.

Dos mecanismos que se complementan:

- Por cuenta: `calcular_fallo` es la regla (N fallos seguidos bloquean M minutos). Vive
  en el documento del agente (`intentos_fallidos`, `bloqueado_hasta`) y la escritura es
  una sola operacion atomica en `app/repos/agentes.py`, que repite esta misma regla como
  actualizacion con pipeline. Frena a quien prueba muchas claves contra UNA cuenta.
- Por IP: `LimitadorIp`, una ventana deslizante en la memoria del proceso (hay un solo
  proceso con el portal). Frena a quien prueba muchas cuentas desde un mismo origen,
  existan o no. No sobrevive a un reinicio y no se comparte entre procesos: es una
  barrera de bajo costo, no un sistema de reputacion.

Ninguna de las dos respuestas revela si la cuenta existe: ver `MENSAJE_CREDENCIALES`.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# El mismo texto para clave mala, cuenta inexistente, cuenta inactiva y bloqueo.
MENSAJE_CREDENCIALES = (
    "Identificador o contraseña incorrectos, o demasiados intentos. "
    "Intenta de nuevo en unos minutos."
)

auditoria = logging.getLogger("centinela.auditoria")

# Tope de IPs recordadas: una ventana por IP, y el diccionario no puede crecer sin limite
# si alguien rota origenes.
MAXIMO_IPS_RECORDADAS = 10_000


@dataclass(frozen=True)
class ResultadoFallo:
    """Como quedo la cuenta despues de contar un intento fallido."""

    intentos_fallidos: int
    bloqueado_hasta: datetime | None
    # True solo para el intento que provoco el bloqueo (para auditarlo una vez).
    bloqueo_nuevo: bool = False


def _aware(fecha: datetime) -> datetime:
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=UTC)


def esta_bloqueada(agente: dict[str, Any] | None, ahora: datetime) -> bool:
    """True si la cuenta tiene un bloqueo todavia vigente."""
    hasta = agente.get("bloqueado_hasta") if agente else None
    return isinstance(hasta, datetime) and _aware(hasta) > ahora


def bloqueo_vencido(agente: dict[str, Any] | None, ahora: datetime) -> bool:
    """True si la cuenta tuvo un bloqueo que ya paso (queda por limpiar)."""
    hasta = agente.get("bloqueado_hasta") if agente else None
    return isinstance(hasta, datetime) and _aware(hasta) <= ahora


def calcular_fallo(
    agente: dict[str, Any],
    *,
    maximo: int,
    bloqueo_minutos: int,
    ahora: datetime,
) -> ResultadoFallo:
    """La regla del contador. Es la fuente de verdad que el repo de Mongo reproduce.

    - Con un bloqueo vigente nada cambia: intentar mas no lo alarga.
    - Con un bloqueo vencido el conteo empieza de nuevo en 1.
    - Al llegar a `maximo` fallos seguidos se fija `bloqueado_hasta = ahora + minutos`.
    """
    hasta = agente.get("bloqueado_hasta")
    previos = int(agente.get("intentos_fallidos", 0) or 0)
    if esta_bloqueada(agente, ahora):
        return ResultadoFallo(previos, _aware(hasta) if isinstance(hasta, datetime) else None)
    intentos = 1 if bloqueo_vencido(agente, ahora) else previos + 1
    if intentos >= maximo:
        return ResultadoFallo(
            intentos, ahora + timedelta(minutes=bloqueo_minutos), bloqueo_nuevo=True
        )
    return ResultadoFallo(intentos, None)


class LimitadorIp:
    """Fallos recientes por IP en una ventana deslizante, en memoria.

    Solo cuentan los fallos: un login correcto desde una oficina con una sola IP no
    gasta el presupuesto de los demas. El reloj se inyecta (`ahora`) para probarlo.
    """

    def __init__(self, maximo: int, ventana_segundos: int) -> None:
        self._maximo = maximo
        self._ventana = timedelta(seconds=ventana_segundos)
        self._fallos: dict[str, deque[datetime]] = {}

    def _podar(self, ip: str, ahora: datetime) -> deque[datetime]:
        cola = self._fallos.get(ip)
        if cola is None:
            return deque()
        limite = ahora - self._ventana
        while cola and cola[0] <= limite:
            cola.popleft()
        if not cola:
            del self._fallos[ip]
            return deque()
        return cola

    def bloqueada(self, ip: str, ahora: datetime | None = None) -> bool:
        return len(self._podar(ip, ahora or datetime.now(UTC))) >= self._maximo

    def registrar_fallo(self, ip: str, ahora: datetime | None = None) -> int:
        """Cuenta un fallo y devuelve cuantos lleva la IP en la ventana."""
        ahora = ahora or datetime.now(UTC)
        cola = self._podar(ip, ahora)
        if not cola:
            if len(self._fallos) >= MAXIMO_IPS_RECORDADAS:
                self._barrer(ahora)
            if len(self._fallos) >= MAXIMO_IPS_RECORDADAS:
                # Sigue lleno de IPs vigentes: se suelta la mas antigua.
                self._fallos.pop(next(iter(self._fallos)))
            self._fallos[ip] = cola
        cola.append(ahora)
        return len(cola)

    def _barrer(self, ahora: datetime) -> None:
        for ip in list(self._fallos):
            self._podar(ip, ahora)

    def olvidar(self, ip: str) -> None:
        self._fallos.pop(ip, None)

    def __len__(self) -> int:
        return len(self._fallos)
