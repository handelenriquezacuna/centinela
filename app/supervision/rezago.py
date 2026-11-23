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
    # Transacciones por segundo que D1 evaluo entre esta medicion y la anterior; `None`
    # en la primera medicion. Es lo que le sirve al analista: cuanto falta, no la fecha
    # de negocio de la mas vieja (D-52).
    ritmo_por_segundo: float | None = None

    @property
    def segundos_para_ponerse_al_dia(self) -> float | None:
        """Estimado a ritmo constante. `None` sin ritmo medido o si D1 no avanzo."""
        if self.pendientes == 0:
            return 0.0
        if not self.ritmo_por_segundo:
            return None
        return self.pendientes / self.ritmo_por_segundo


def ritmo(
    eventos_antes: int, momento_antes: float, eventos_ahora: int, momento_ahora: float
) -> float | None:
    """Eventos por segundo entre dos lecturas de reloj monotono; `None` si no hay intervalo."""
    intervalo = momento_ahora - momento_antes
    if intervalo <= 0:
        return None
    return max(0, eventos_ahora - eventos_antes) / intervalo
