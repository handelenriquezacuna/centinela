"""Comprobar usuario y contrasena. La unica puerta por la que se obtiene una sesion."""

from __future__ import annotations

from typing import Any

from app.repos.agentes import ProtocoloRepoAgentes
from app.seguridad import contrasenas


async def autenticar(
    repo: ProtocoloRepoAgentes, agente_id: str, contrasena: str
) -> dict[str, Any] | None:
    """El documento del agente si las credenciales sirven; `None` en cualquier otro caso.

    No distingue "no existe", "esta inactivo", "no tiene credencial" y "contrasena
    mala": para quien llama es lo mismo, y asi tampoco se filtra cual es cual. El hash
    se calcula siempre (aun sin credencial) para que el tiempo tampoco lo delate.
    """
    agente = await repo.obtener(agente_id)
    credencial = agente.get("credencial") if agente else None
    correcta = contrasenas.verificar(contrasena, credencial)
    if agente is None or not correcta or not agente.get("activo", True):
        return None
    return agente
