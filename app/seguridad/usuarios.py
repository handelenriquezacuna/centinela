"""Crear o cambiar la contrasena (y el rol) de un agente: `python -m app.seguridad.usuarios`.

    python -m app.seguridad.usuarios --agente AGT-001 --rol supervisor

La contrasena se pide con `getpass` (no queda en el historial de la terminal ni en la
lista de procesos) y se pide dos veces. Ninguna contrasena vive en el repositorio: la
semilla deja a los agentes sin credencial, y esta herramienta es la unica forma de
dársela. El agente tiene que existir; la herramienta no crea agentes.

`--rol` es opcional: sin el, se conserva el rol que el agente ya tenia.

    python -m app.seguridad.usuarios --agente AGT-001 --desbloquear

`--desbloquear` (T-83) quita el bloqueo temporal por intentos fallidos y reinicia el
contador, sin tocar la contrasena ni pedirla. Queda en el log `centinela.auditoria`.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
from collections.abc import Sequence

from app.modelos.alertas import RolAgente
from app.nucleo.config import obtener_ajustes
from app.nucleo.db import ciclo_cliente, obtener_base
from app.repos.agentes import ProtocoloRepoAgentes, RepoAgentes
from app.seguridad import contrasenas
from app.seguridad.bloqueo import auditoria


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


async def desbloquear_cuenta(repo: ProtocoloRepoAgentes, agente_id: str) -> None:
    """Quita el bloqueo y el contador. `ValueError` si el agente no existe."""
    if not await repo.desbloquear(agente_id):
        raise ValueError(f"No existe el agente {agente_id}")
    auditoria.warning("desbloqueo agente=%s motivo=administrador via=tareas", agente_id)


def _leer_contrasena() -> str:
    primera = getpass.getpass("Contrasena nueva: ")
    if primera != getpass.getpass("Repetirla: "):
        raise ValueError("Las contrasenas no coinciden")
    return primera


async def _principal(argumentos: argparse.Namespace) -> None:
    ajustes = obtener_ajustes()
    if argumentos.desbloquear:
        async with ciclo_cliente(ajustes) as cliente:
            await desbloquear_cuenta(RepoAgentes(obtener_base(cliente, ajustes)), argumentos.agente)
        print(f"Listo: {argumentos.agente} desbloqueado; su contador de intentos quedo en cero.")
        return
    contrasena = _leer_contrasena()
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
    analizador.add_argument(
        "--desbloquear",
        action="store_true",
        help="Quita el bloqueo por intentos fallidos (no pide ni cambia la contrasena)",
    )
    argumentos = analizador.parse_args(argv)
    if argumentos.desbloquear and argumentos.rol:
        analizador.error("--desbloquear no se combina con --rol")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        asyncio.run(_principal(argumentos))
    except ValueError as fallo:
        print(fallo, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
