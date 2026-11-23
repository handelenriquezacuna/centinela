"""Crear o cambiar la contrasena (y el rol) de un agente: `python -m app.seguridad.usuarios`.

    python -m app.seguridad.usuarios --agente AGT-001 --rol supervisor

La contrasena se pide con `getpass` (no queda en el historial de la terminal ni en la
lista de procesos) y se pide dos veces. Ninguna contrasena vive en el repositorio: la
semilla deja a los agentes sin credencial, y esta herramienta es la unica forma de
dársela. El agente tiene que existir; la herramienta no crea agentes.

`--rol` es opcional: sin el, se conserva el rol que el agente ya tenia.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from app.modelos.alertas import RolAgente
from app.nucleo.config import obtener_ajustes
from app.nucleo.db import ciclo_cliente, obtener_base
from app.repos.agentes import ProtocoloRepoAgentes, RepoAgentes
from app.seguridad import contrasenas


async def asignar_credencial(
    repo: ProtocoloRepoAgentes,
    agente_id: str,
    contrasena: str,
    rol: RolAgente | None = None,
) -> None:
    """Cifra y guarda. Lanza `ValueError` con un mensaje para la persona si no se puede."""
    problemas = contrasenas.problemas_de_contrasena(contrasena)
    if problemas:
        raise ValueError("Contrasena rechazada: " + "; ".join(problemas))
    if await repo.obtener(agente_id) is None:
        raise ValueError(f"No existe el agente {agente_id}")
    await repo.guardar_credencial(
        agente_id, contrasenas.cifrar(contrasena), rol.value if rol else None
    )


def _leer_contrasena() -> str:
    primera = getpass.getpass("Contrasena nueva: ")
    if primera != getpass.getpass("Repetirla: "):
        raise ValueError("Las contrasenas no coinciden")
    return primera


async def _principal(argumentos: argparse.Namespace) -> None:
    contrasena = _leer_contrasena()
    ajustes = obtener_ajustes()
    async with ciclo_cliente(ajustes) as cliente:
        repo = RepoAgentes(obtener_base(cliente, ajustes))
        await asignar_credencial(
            repo, argumentos.agente, contrasena, RolAgente(argumentos.rol) if argumentos.rol else None
        )
    print(f"Listo: {argumentos.agente} ya puede iniciar sesion.")


def main(argv: Sequence[str] | None = None) -> int:
    analizador = argparse.ArgumentParser(
        prog="python -m app.seguridad.usuarios",
        description="Crea o cambia la contrasena de un agente.",
    )
    analizador.add_argument("--agente", required=True, help="Identificador, ej. AGT-001")
    analizador.add_argument(
        "--rol", choices=[rol.value for rol in RolAgente], help="Rol a asignar (opcional)"
    )
    argumentos = analizador.parse_args(argv)
    try:
        asyncio.run(_principal(argumentos))
    except ValueError as fallo:
        print(fallo, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
