"""Limite de abuso del endpoint publico del cliente (T-82), en memoria.

Un solo proceso (la deteccion exige un solo worker), asi que un contador en memoria alcanza:
no hace falta Redis ni una coleccion. Si algun dia hubiera varios procesos, cada uno
limitaria por su cuenta y el limite efectivo seria N veces mayor; es el costo asumido.

Dos contadores, de ventana deslizante:

- **Enlaces invalidos por IP.** Cada enlace que no existe, vencio o no tiene forma de token
  cuenta contra la IP. Pasado el limite, esa IP recibe 429 incluso con un enlace bueno, hasta
  que la ventana se vacie: quien prueba enlaces al azar se queda sin intentos.
- **Respuestas por token e IP.** Cuantas veces se intenta responder con el mismo enlace
  desde la misma IP. El enlace ya es de un solo uso; esto frena a quien insiste en bucle.

El reloj es inyectable para probar el vencimiento de la ventana sin dormir.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any


class LimitadorIntentos:
    def __init__(
        self,
        *,
        invalidos_por_ip: int,
        respuestas_por_enlace: int,
        ventana_segundos: float,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._invalidos_por_ip = invalidos_por_ip
        self._respuestas_por_enlace = respuestas_por_enlace
        self._ventana = ventana_segundos
        self._reloj = reloj
        self._invalidos: dict[str, deque[float]] = {}
        self._respuestas: dict[tuple[str, str], deque[float]] = {}

    def _vigentes(self, tabla: dict[Any, deque[float]], clave: Any) -> deque[float]:
        marcas = tabla.get(clave)
        if marcas is None:
            return deque()
        corte = self._reloj() - self._ventana
        while marcas and marcas[0] <= corte:
            marcas.popleft()
        if not marcas:
            del tabla[clave]
            return deque()
        return marcas

    def _anotar(self, tabla: dict[Any, deque[float]], clave: Any) -> None:
        marcas = self._vigentes(tabla, clave)
        marcas.append(self._reloj())
        tabla[clave] = marcas

    def ip_bloqueada(self, ip: str) -> bool:
        return len(self._vigentes(self._invalidos, ip)) >= self._invalidos_por_ip

    def anotar_invalido(self, ip: str) -> None:
        self._anotar(self._invalidos, ip)

    def segundos_de_espera(self, ip: str) -> int:
        """Cuanto falta para que la IP salga del bloqueo (para `Retry-After`)."""
        marcas = self._vigentes(self._invalidos, ip)
        if not marcas:
            return 1
        return max(1, int(marcas[0] + self._ventana - self._reloj()) + 1)

    def intento_de_respuesta(self, token_hash: str, ip: str) -> bool:
        """Cuenta un intento de responder; `False` si ya paso el tope de la ventana."""
        clave = (token_hash, ip)
        if len(self._vigentes(self._respuestas, clave)) >= self._respuestas_por_enlace:
            return False
        self._anotar(self._respuestas, clave)
        return True
