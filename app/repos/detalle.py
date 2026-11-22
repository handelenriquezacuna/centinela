"""Lecturas de la pantalla de detalle de alerta (H-18).

La alerta ya trae copiados los campos de la fila, pero el detalle muestra mas: la
transaccion completa, el perfil del cliente con que se evaluo, los agentes entre los que
elegir y el caso que abrio una escalacion. Son lecturas de apoyo sobre colecciones que
otros repositorios no exponen; se juntan aqui para no tocar los existentes.

No es una consulta por pantalla ni un `$lookup`: son cuatro lecturas por `_id` (o por
indice) y el detalle se abre de a una alerta.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.modelos import COLECCION_CASOS, COLECCION_TRANSACCIONES

COLECCION_CUENTAS = "cuentas"
COLECCION_CLIENTES = "clientes"
COLECCION_PERFILES = "perfiles_comportamiento"
COLECCION_AGENTES = "agentes"


@runtime_checkable
class ProtocoloRepoDetalle(Protocol):
    """Lo que el detalle necesita. El doble de `pruebas/dobles/` lo cumple."""

    async def contexto(self, transaccion_id: str) -> dict[str, Any]:
        """`{transaccion, cliente, perfil}`; cada pieza es `None` si no existe."""
        ...

    async def agentes(self) -> list[dict[str, Any]]: ...

    async def caso_de_alerta(self, alerta_id: str) -> dict[str, Any] | None: ...


class RepoDetalle:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def contexto(self, transaccion_id: str) -> dict[str, Any]:
        transaccion = await self._base[COLECCION_TRANSACCIONES].find_one(
            {"_id": transaccion_id}
        )
        cliente = None
        perfil = None
        if transaccion is not None:
            # transacciones.cuenta_origen es CTA-001; la cuenta dice de quien es.
            cuenta = await self._base[COLECCION_CUENTAS].find_one(
                {"_id": transaccion["cuenta_origen"]}
            )
            if cuenta is not None:
                cliente_id = cuenta["cliente_id"]
                cliente = await self._base[COLECCION_CLIENTES].find_one({"_id": cliente_id})
                perfil = await self._base[COLECCION_PERFILES].find_one({"_id": cliente_id})
        return {"transaccion": transaccion, "cliente": cliente, "perfil": perfil}

    async def agentes(self) -> list[dict[str, Any]]:
        cursor = self._base[COLECCION_AGENTES].find({}).sort("_id", ASCENDING)
        return await cursor.to_list(length=None)

    async def caso_de_alerta(self, alerta_id: str) -> dict[str, Any] | None:
        return await self._base[COLECCION_CASOS].find_one({"alertas": alerta_id})
