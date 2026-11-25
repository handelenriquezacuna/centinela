"""H-22 · Disparador D3: recalcula los indicadores del dia actual cada N segundos.

T-63: la misma vuelta de D3 recalcula tambien los rasgos de grafo que lee R-11, si el
repositorio de rasgos esta presente y la regla red_mulas esta activa. Cada trabajo falla por
separado: un fallo de los rasgos se anota en `/estado` pero no impide los indicadores, y
viceversa. El latido se da si los dos salieron bien.

T-76 (H-21): si se le pasa `repo_perfiles`, D3 recalcula tambien `perfiles_comportamiento`,
pero cada `perfiles.intervalo_segundos` (por omision una hora) y no en cada vuelta. Es opt-in:
`main.py` solo lo conecta con `perfiles.activo`, porque recalcular perfiles cambia lo que R-01
y R-05 deciden. Un fallo se anota y no impide lo demas; tampoco se reintenta en la vuelta
siguiente sino en el proximo intervalo.

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
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from functools import partial

from app.deteccion.indicadores import ProtocoloRepoIndicadores
from app.deteccion.perfiles import ProtocoloRepoPerfiles
from app.deteccion.rasgos_grafo import ProtocoloRepoRasgos
from app.nucleo.config import AjustesIndicadores, AjustesPerfiles
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
    repo_rasgos: ProtocoloRepoRasgos | None = None,
    ahora: Callable[[], datetime] = lambda: datetime.now(UTC),
    repo_perfiles: ProtocoloRepoPerfiles | None = None,
    ajustes_perfiles: AjustesPerfiles | None = None,
    reloj: Callable[[], float] = time.monotonic,
) -> None:
    """Bucle infinito de D3. Lo lanza el lifespan: `supervisor.lanzar(NOMBRE_D3, d3(...))`."""
    ajustes_perfiles = ajustes_perfiles or AjustesPerfiles()
    proximo_perfiles = reloj()  # el primer recalculo de perfiles es en la primera vuelta
    while True:
        dia = hoy()
        if await _intentar(supervisor, nombre, "indicadores", repo.recalcular, dia, dia):
            supervisor.latir(nombre)
        if repo_rasgos is not None:
            await _intentar(
                supervisor, nombre, "rasgos de grafo", repo_rasgos.recalcular_si_activa, ahora()
            )
        if repo_perfiles is not None and reloj() >= proximo_perfiles:
            proximo_perfiles = reloj() + ajustes_perfiles.intervalo_segundos
            await _intentar(
                supervisor,
                nombre,
                "perfiles",
                partial(repo_perfiles.recalcular, dias=ajustes_perfiles.dias),
                ahora(),
            )
        await asyncio.sleep(ajustes.intervalo_segundos)


async def _intentar(
    supervisor: Supervisor,
    nombre: str,
    trabajo: str,
    correr: Callable[..., Awaitable[object]],
    *argumentos: object,
) -> bool:
    try:
        await correr(*argumentos)
    except asyncio.CancelledError:
        raise
    except Exception as fallo:  # noqa: BLE001 - D3 nunca tumba el proceso
        supervisor.anotar_error(nombre, fallo)
        registro_log.error("%s: fallo el recalculo de %s: %s", nombre, trabajo, fallo)
        return False
    return True
