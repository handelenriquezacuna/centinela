"""Rezago de una tarea: cuanto trabajo tiene pendiente (T-57).

Para D1 es "transacciones insertadas que todavia no evaluo" y la antiguedad de la mas
vieja. Es lo que dice si D1 se esta atrasando ante un volumen grande, algo que ni el
latido ni la latencia (que solo mide las alertas ya escritas) muestran.

Parte pura: recibe lo ya medido. Quien mide (D1, con una consulta de `app/repos/`)
decide como; el supervisor solo guarda la ultima medida y la publica en `/estado`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ResumenRezago:
    """Ultima medicion del rezago de una tarea.

    `antiguedad_segundos` es `None` cuando no hay pendientes. `medido_en` (UTC) deja ver
    que tan fresca es la cifra: si D1 se atasca, la medida deja de renovarse.
    """

    pendientes: int
    antiguedad_segundos: float | None
    medido_en: datetime
