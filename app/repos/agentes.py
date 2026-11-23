"""Agentes como usuarios del sistema: leer su credencial y guardar una nueva (H-25).

Es el unico lugar que escribe `agentes.credencial`. La contrasena nunca llega aqui: se
recibe ya cifrada (`app/seguridad/contrasenas.py`).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pymongo.asynchronous.database import AsyncDatabase

COLECCION_AGENTES = "agentes"


@runtime_checkable
class ProtocoloRepoAgentes(Protocol):
    async def obtener(self, agente_id: str) -> dict[str, Any] | None: ...

    async def guardar_credencial(
        self, agente_id: str, credencial: dict[str, Any], rol: str | None = None
    ) -> bool: ...


class RepoAgentes:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def obtener(self, agente_id: str) -> dict[str, Any] | None:
        return await self._base[COLECCION_AGENTES].find_one({"_id": agente_id})

    async def guardar_credencial(
        self, agente_id: str, credencial: dict[str, Any], rol: str | None = None
    ) -> bool:
        """Crea o reemplaza la credencial (y el rol, si viene). False si no existe el agente."""
        cambios: dict[str, Any] = {"credencial": credencial}
        if rol is not None:
            cambios["rol"] = rol
        resultado = await self._base[COLECCION_AGENTES].update_one(
            {"_id": agente_id}, {"$set": cambios}
        )
        return resultado.matched_count == 1
