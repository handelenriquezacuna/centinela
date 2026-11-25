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
    # Red de seguridad independiente del orden de los ids: segundos entre que Mongo inserto
    # la ultima transaccion que D1 termino y el momento en que D1 la termino; 0 cuando la
    # vuelta del cursor llega vacia (D1 esta al dia). `None` si D1 aun no vio ninguna
    # vuelta o el evento no trajo hora. No depende de `_id`.
    atraso_segundos: float | None = None

    @property
    def segundos_para_ponerse_al_dia(self) -> float | None:
        """Estimado a ritmo constante. `None` sin ritmo medido o si D1 no avanzo."""
        if self.pendientes == 0:
            return 0.0
        if not self.ritmo_por_segundo:
            return None
        return self.pendientes / self.ritmo_por_segundo


def aviso_por_conteo(medida: ResumenRezago | None, aviso_desde: int) -> bool:
    """Hay mas pendientes que `deteccion.rezago_aviso_desde`."""
    return medida is not None and medida.pendientes > aviso_desde


def aviso_por_atraso(medida: ResumenRezago | None, atraso_desde: float) -> bool:
    """D1 va mas de `deteccion.rezago_aviso_atraso_segundos` detras de la ingesta.

    Es la red de seguridad: no mira ids, asi que avisa aunque el conteo diga 0.
    """
    return (
        medida is not None
        and medida.atraso_segundos is not None
        and medida.atraso_segundos > atraso_desde
    )


def ritmo(
    eventos_antes: int, momento_antes: float, eventos_ahora: int, momento_ahora: float
) -> float | None:
    """Eventos por segundo entre dos lecturas de reloj monotono; `None` si no hay intervalo."""
    intervalo = momento_ahora - momento_antes
    if intervalo <= 0:
        return None
    return max(0, eventos_ahora - eventos_antes) / intervalo
