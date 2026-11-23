"""CLI de la carga: `python -m app.carga --archivo transacciones.jsonl`.

La conexion sale de la misma configuracion que usa la API (incluida la variable
`CENTINELA_MONGO__URI`). Codigos de salida: 0 todo bien (las repetidas no son error),
1 hubo lineas rechazadas, 2 el archivo no existe.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.carga.lotes import TAMANO_LOTE, ResumenCarga, cargar
from app.nucleo.config import obtener_ajustes
from app.nucleo.db import ciclo_cliente, obtener_base
from app.repos.carga_transacciones import RepoCargaTransacciones

MAXIMO_RECHAZADAS_IMPRESAS = 20


def construir_analizador() -> argparse.ArgumentParser:
    analizador = argparse.ArgumentParser(
        prog="python tareas.py cargar",
        description="Carga transacciones desde un archivo JSONL, en lotes, sin duplicar.",
    )
    analizador.add_argument("--archivo", required=True, type=Path, help="archivo .jsonl")
    analizador.add_argument(
        "--lote", type=int, default=TAMANO_LOTE, help=f"tamano del lote (por omision {TAMANO_LOTE})"
    )
    return analizador


def imprimir(resumen: ResumenCarga) -> None:
    print(
        f"cargadas: {resumen.cargadas}  repetidas: {resumen.repetidas}  "
        f"rechazadas: {len(resumen.rechazadas)}  "
        f"({resumen.segundos:.2f} s, {resumen.transacciones_por_segundo:.0f} transacciones/s)"
    )
    for rechazada in resumen.rechazadas[:MAXIMO_RECHAZADAS_IMPRESAS]:
        print(f"  linea {rechazada.numero}: {rechazada.motivo}")
    resto = len(resumen.rechazadas) - MAXIMO_RECHAZADAS_IMPRESAS
    if resto > 0:
        print(f"  ... y {resto} mas")


async def principal(archivo: Path, lote: int) -> int:
    ajustes = obtener_ajustes()
    async with ciclo_cliente(ajustes) as cliente:
        repo = RepoCargaTransacciones(obtener_base(cliente, ajustes))
        with archivo.open(encoding="utf-8") as lineas:
            resumen = await cargar(repo, lineas, tamano_lote=lote)
    imprimir(resumen)
    return 1 if resumen.rechazadas else 0


def main(argv: list[str] | None = None) -> int:
    argumentos = construir_analizador().parse_args(argv)
    if not argumentos.archivo.is_file():
        print(f"ERROR: no existe el archivo {argumentos.archivo}", file=sys.stderr)
        return 2
    if argumentos.lote < 1:
        print("ERROR: --lote tiene que ser positivo", file=sys.stderr)
        return 2
    return asyncio.run(principal(argumentos.archivo, argumentos.lote))


if __name__ == "__main__":
    sys.exit(main())
