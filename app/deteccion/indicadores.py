"""H-22 · Recalculo de `indicadores_diarios` (vista materializada).

    uv run python -m app.deteccion.indicadores                       # hoy (hora de CR)
    uv run python -m app.deteccion.indicadores --desde 2026-11-01 --hasta 2026-11-22

o `python tareas.py indicadores [--desde ... --hasta ...]`. Es reejecutable: el pipeline
de `app/repos/indicadores.py` reemplaza el documento de cada dia (`$merge`), no lo suma.

Este modulo no consulta Mongo: pide el recalculo al repositorio, y D3 (`d3.py`) hace lo
mismo cada N segundos para el dia actual.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from typing import Protocol

from app.repos.indicadores import hoy_costa_rica


class ProtocoloRepoIndicadores(Protocol):
    async def recalcular(self, desde: date, hasta: date) -> int: ...


def leer_argumentos(argumentos: list[str] | None = None) -> tuple[date, date]:
    """Rango pedido; sin banderas, el dia actual de Costa Rica."""
    analizador = argparse.ArgumentParser(
        prog="python -m app.deteccion.indicadores",
        description="Recalcula indicadores_diarios (H-22).",
    )
    analizador.add_argument("--desde", type=date.fromisoformat, help="YYYY-MM-DD")
    analizador.add_argument("--hasta", type=date.fromisoformat, help="YYYY-MM-DD")
    args = analizador.parse_args(argumentos)

    if (args.desde is None) != (args.hasta is None):
        analizador.error("--desde y --hasta van juntos")
    if args.desde is None:
        hoy = hoy_costa_rica()
        return hoy, hoy
    if args.hasta < args.desde:
        analizador.error("--hasta no puede ser anterior a --desde")
    return args.desde, args.hasta


async def recalcular_rango(repo: ProtocoloRepoIndicadores, desde: date, hasta: date) -> int:
    return await repo.recalcular(desde, hasta)


async def _principal(argumentos: list[str] | None = None) -> None:
    from app.nucleo.config import obtener_ajustes
    from app.nucleo.db import ciclo_cliente, obtener_base
    from app.repos.indicadores import RepoIndicadores

    desde, hasta = leer_argumentos(argumentos)
    ajustes = obtener_ajustes()
    async with ciclo_cliente(ajustes) as cliente:
        repo = RepoIndicadores(obtener_base(cliente, ajustes))
        dias = await recalcular_rango(repo, desde, hasta)
        filas = await repo.leer_rango(desde, hasta)
    print(f"indicadores_diarios: {dias} dia(s) recalculados ({desde} a {hasta})")
    for fila in filas:
        print(
            f"  {fila['fecha']}  alertas={fila['total_alertas']:>5}  "
            f"severidad={dict(fila['por_severidad'])}  estado={dict(fila['por_estado'])}"
        )


if __name__ == "__main__":
    asyncio.run(_principal())
