"""Latencia de una tarea: ventana movil en memoria y sus percentiles.

Es la medida de H-09 ("la alerta aparece en menos de X segundos"): desde que la
transaccion quedo insertada en Mongo hasta que D1 termino de escribir su alerta. La
ventana guarda las ultimas `capacidad` mediciones (por omision 500) y nada mas: no es
una serie historica, es lo que `/estado` necesita para decir como esta yendo ahora.

Es una parte pura, sin Mongo ni reloj: recibe milisegundos ya medidos. Quien mide
(D1) decide desde donde y hasta donde.

Percentil por rango mas cercano (nearest-rank): el valor ordenado en la posicion
`ceil(p/100 * n)`. No interpola, asi que el resultado es siempre una medicion que de
verdad ocurrio, y con pocas muestras no inventa un numero entre dos. Con n = 1 todos
los percentiles son esa unica medicion.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

CAPACIDAD_POR_OMISION = 500


@dataclass(frozen=True)
class ResumenLatencia:
    """Lo que `/estado` publica. `n` es cuantas mediciones hay hoy en la ventana.

    Con `n == 0` los demas campos son `None`: no hay nada que resumir, y un cero
    seria una medicion falsa.
    """

    n: int
    p50: float | None
    p95: float | None
    max: float | None


def percentil(ordenados: list[float], porcentaje: float) -> float:
    """Percentil por rango mas cercano de una lista YA ordenada y no vacia."""
    if not ordenados:
        raise ValueError("no hay mediciones para calcular un percentil")
    if not 0 < porcentaje <= 100:
        raise ValueError("el porcentaje tiene que estar en (0, 100]")
    posicion = math.ceil(porcentaje / 100 * len(ordenados))
    return ordenados[posicion - 1]


def resumir(mediciones: Iterable[float]) -> ResumenLatencia:
    """p50, p95, maximo y cantidad de una coleccion de mediciones, en milisegundos."""
    ordenadas = sorted(mediciones)
    if not ordenadas:
        return ResumenLatencia(n=0, p50=None, p95=None, max=None)
    return ResumenLatencia(
        n=len(ordenadas),
        p50=round(percentil(ordenadas, 50), 1),
        p95=round(percentil(ordenadas, 95), 1),
        max=round(ordenadas[-1], 1),
    )


class VentanaLatencia:
    """Las ultimas `capacidad` latencias. Al llenarse, la mas vieja sale."""

    def __init__(self, capacidad: int = CAPACIDAD_POR_OMISION) -> None:
        if capacidad < 1:
            raise ValueError("la capacidad de la ventana tiene que ser positiva")
        self._mediciones: deque[float] = deque(maxlen=capacidad)

    def registrar(self, milisegundos: float) -> None:
        """Una medicion. Un negativo (relojes de dos maquinas desfasados) se lleva a 0."""
        self._mediciones.append(max(0.0, float(milisegundos)))

    def resumen(self) -> ResumenLatencia:
        return resumir(self._mediciones)

    def __len__(self) -> int:
        return len(self._mediciones)
