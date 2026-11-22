"""CLI del generador: `python -m app.generador`.

Los valores por omision salen del bloque `generador` de `config/centinela.yml`; cada
bandera los sobrescribe para esa corrida. La conexion sale de la misma configuracion que
usa la API (variables `CENTINELA_MONGO__URI` y `CENTINELA_MONGO__BASE` incluidas).

Vuelve a correr sin problema: borra lo que genero antes (identificadores de cuatro o mas
digitos y transacciones etiquetadas) y no toca los datos de la Practica 1 ni el demo.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from app.generador.ejecucion import Resumen, ejecutar
from app.nucleo.config import AjustesGenerador, obtener_ajustes
from app.nucleo.db import ciclo_cliente, obtener_base
from app.repos.generador import RepoGenerador


def construir_analizador() -> argparse.ArgumentParser:
    analizador = argparse.ArgumentParser(
        prog="python -m app.generador",
        description="Genera clientes, cuentas, perfiles, transacciones legitimas y fraude.",
    )
    analizador.add_argument("--clientes", type=int, help="numero de clientes")
    analizador.add_argument("--transacciones", type=int, help="total de transacciones")
    analizador.add_argument("--ritmo", type=int, help="transacciones por segundo (0 = sin limite)")
    analizador.add_argument("--semilla", type=int, help="semilla aleatoria")
    analizador.add_argument(
        "--proporcion", type=float, help="fraccion de fraude, estrictamente menor a 0.02"
    )
    return analizador


def ajustes_de_la_corrida(argumentos: argparse.Namespace) -> AjustesGenerador:
    """Los ajustes del YAML con las banderas encima, revalidados."""
    base = obtener_ajustes().generador
    cambios: dict[str, Any] = {
        "clientes": argumentos.clientes,
        "transacciones": argumentos.transacciones,
        "ritmo_por_segundo": argumentos.ritmo,
        "semilla": argumentos.semilla,
        "proporcion_anomalos": argumentos.proporcion,
    }
    datos = base.model_dump()
    datos.update({clave: valor for clave, valor in cambios.items() if valor is not None})
    return AjustesGenerador.model_validate(datos)


def imprimir(resumen: Resumen) -> None:
    total = resumen.transacciones
    porcentaje = 100 * resumen.total_fraude / total if total else 0.0
    print(f"clientes: {resumen.clientes}  cuentas: {resumen.cuentas}  perfiles: {resumen.perfiles}")
    print(
        f"transacciones: {total} en {resumen.segundos:.2f} s "
        f"({resumen.documentos_por_segundo:.0f} docs/s)"
    )
    print(f"mulas reportadas en listas_riesgo: {resumen.listas_riesgo}")
    print(f"fraude: {resumen.total_fraude} ({porcentaje:.2f} %)")
    for patron, cantidad in sorted(resumen.fraude_por_patron.items()):
        print(f"  {patron}: {cantidad}")


async def principal(argumentos: argparse.Namespace) -> int:
    ajustes = obtener_ajustes()
    generador = ajustes_de_la_corrida(argumentos)
    async with ciclo_cliente(ajustes) as cliente:
        resumen = await ejecutar(RepoGenerador(obtener_base(cliente, ajustes)), generador)
    imprimir(resumen)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(principal(construir_analizador().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
