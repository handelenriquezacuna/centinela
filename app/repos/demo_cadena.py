"""T-63 / D-68 · Lo que `python -m app.deteccion.demo_cadena` lee y escribe en Mongo.

Solo inserta transacciones nuevas (nunca borra ni modifica) y lee `cuentas` para resolver
los numeros SINPE de la cadena del demo. Los identificadores los asigna `app/repos/ids_transaccion.py`
(el siguiente al mayor TXN de seis digitos): el rezago de D1 compara `_id` como texto
(`filtro_pendientes`), y un rango propio dejaria el aviso de rezago en 0 con miles pendientes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.modelos import COLECCION_TRANSACCIONES, Transaccion
from app.repos.carga_demo import a_entero_64
from app.repos.ids_transaccion import siguiente_numero_transaccion


class RepoDemoCadena:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base
        self._transacciones = base[COLECCION_TRANSACCIONES]

    async def cuentas_por_sinpe(self, sinpes: Sequence[str]) -> dict[str, str]:
        """`cuenta_sinpe -> cuentas._id` de las que existan."""
        docs = self._base["cuentas"].find({"cuenta_sinpe": {"$in": list(sinpes)}})
        return {doc["cuenta_sinpe"]: doc["_id"] async for doc in docs}

    async def siguiente_numero(self) -> int:
        """Numero que sigue al mayor `TXN-NNNNNN` de la base (el asignador comun)."""
        return await siguiente_numero_transaccion(self._base)

    async def insertar(self, transacciones: Sequence[Transaccion]) -> None:
        """Inserta en orden. Una colision de `_id` (otro proceso inserto en medio) sube."""
        for transaccion in transacciones:
            try:
                await self._transacciones.insert_one(a_entero_64(transaccion.a_documento()))
            except DuplicateKeyError as fallo:
                raise RuntimeError(
                    f"{transaccion.transaccion_id} ya existe; vuelve a correr el comando"
                ) from fallo

    async def alerta_de(self, transaccion_id: str) -> dict[str, Any] | None:
        return await self._base["alertas"].find_one({"transaccion_id": transaccion_id})
