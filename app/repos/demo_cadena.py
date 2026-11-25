"""T-63 / D-68 · Lo que `python -m app.deteccion.demo_cadena` lee y escribe en Mongo.

Solo inserta transacciones nuevas (nunca borra ni modifica) y lee `cuentas` para resolver
los numeros SINPE de la cadena del demo. Los identificadores son de seis digitos y del rango
TXN-9NNNNN (el siguiente libre desde TXN-900001): el rezago de D1 compara `_id` como texto
(`filtro_pendientes`), y un id de ancho distinto o menor que los del generador (TXN-000001...)
dejaria el aviso de rezago en 0 con miles pendientes.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.modelos import COLECCION_TRANSACCIONES, Transaccion
from app.repos.carga_demo import a_entero_64

_NUMERO = re.compile(r"^TXN-(\d+)$")
ANCHO = 6
PRIMER_NUMERO = 900_001  # TXN-900001: por encima de cualquier id del generador


class RepoDemoCadena:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base
        self._transacciones = base[COLECCION_TRANSACCIONES]

    async def cuentas_por_sinpe(self, sinpes: Sequence[str]) -> dict[str, str]:
        """`cuenta_sinpe -> cuentas._id` de las que existan."""
        docs = self._base["cuentas"].find({"cuenta_sinpe": {"$in": list(sinpes)}})
        return {doc["cuenta_sinpe"]: doc["_id"] async for doc in docs}

    async def siguiente_numero(self) -> int:
        """Numero que sigue al mayor `TXN-9NNNNN`; `PRIMER_NUMERO` si no hay ninguno."""
        ultima = await self._transacciones.find_one(
            {"_id": {"$regex": "^TXN-9[0-9]{5}$"}}, {"_id": 1}, sort=[("_id", -1)]
        )
        if ultima is None:
            return PRIMER_NUMERO
        coincidencia = _NUMERO.match(ultima["_id"])
        return int(coincidencia.group(1)) + 1 if coincidencia else PRIMER_NUMERO

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
