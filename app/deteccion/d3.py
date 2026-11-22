"""H-22 · Disparador D3: recalcula los indicadores del dia actual cada N segundos.

D3 no escucha nada: es un temporizador. Cada `indicadores.intervalo_segundos` pide al
repositorio recalcular el dia de hoy (hora de Costa Rica) y late. Si un recalculo falla,
se anota el error en `/estado` y se reintenta en la vuelta siguiente (la espera es el
propio intervalo). Es idempotente: recalcular reemplaza el documento del dia.

Un dia que cambia (medianoche de CR) se maneja solo: cada vuelta calcula el "hoy" de
ese momento; el ultimo recalculo del dia anterior queda como estaba.

Este modulo no consulta Mongo y no importa `app/api/`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date

from app.deteccion.indicadores import ProtocoloRepoIndicadores
from app.nucleo.config import AjustesIndicadores
from app.repos.indicadores import hoy_costa_rica
from app.supervision import Supervisor

registro_log = logging.getLogger("centinela.d3")

NOMBRE_D3 = "d3_indicadores"


async def d3(
    repo: ProtocoloRepoIndicadores,
    supervisor: Supervisor,
    *,
    ajustes: AjustesIndicadores,
    nombre: str = NOMBRE_D3,
    hoy: Callable[[], date] = hoy_costa_rica,
) -> None:
    """Bucle infinito de D3. Lo lanza el lifespan: `supervisor.lanzar(NOMBRE_D3, d3(...))`."""
    while True:
        try:
            dia = hoy()
            await repo.recalcular(dia, dia)
            supervisor.latir(nombre)
        except asyncio.CancelledError:
            raise
        except Exception as fallo:  # noqa: BLE001 - D3 nunca tumba el proceso
            supervisor.anotar_error(nombre, fallo)
            registro_log.error("%s: fallo el recalculo: %s", nombre, fallo)
        await asyncio.sleep(ajustes.intervalo_segundos)
