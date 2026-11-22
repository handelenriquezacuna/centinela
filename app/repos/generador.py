"""Escrituras del generador de datos sinteticos (H-03, H-04, H-05).

Vive en `app/repos/` porque ninguna consulta a Mongo va fuera de aqui. No decide que se
genera (eso es `app/generador/`): solo escribe lo que le dan, por lotes.

Dinero: los campos `*_crc` se escriben como `Int64` (el validador pide `long`) con el
mismo `a_entero_64` de la carga de demo.

Idempotencia: el generador usa identificadores de cuatro o mas digitos para clientes y
cuentas (`CLI-0001`) seis para transacciones y cuatro para `listas_riesgo` (`RSK-0001`), y marca cada transaccion con
`simulacion`. Con eso `limpiar_generado` borra lo suyo sin tocar los documentos de
tres digitos de la Practica 1 ni el demo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.repos.carga_demo import a_entero_64

_ID_GENERADO_CLIENTE = {"_id": {"$regex": r"^CLI-[0-9]{4,}$"}}
_ID_GENERADO_RIESGO = {"_id": {"$regex": r"^RSK-[0-9]{4,}$"}}
_ID_GENERADO_CUENTA = {"_id": {"$regex": r"^CTA-[0-9]{4,}$"}}


class RepoGenerador:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def limpiar_generado(self) -> dict[str, int]:
        """Borra lo que escribio una corrida anterior; deja intacto el resto."""
        borrados = {
            "transacciones": await self._base["transacciones"].delete_many(
                {"simulacion": {"$exists": True}}
            ),
            "perfiles_comportamiento": await self._base["perfiles_comportamiento"].delete_many(
                _ID_GENERADO_CLIENTE
            ),
            "cuentas": await self._base["cuentas"].delete_many(_ID_GENERADO_CUENTA),
            "listas_riesgo": await self._base["listas_riesgo"].delete_many(_ID_GENERADO_RIESGO),
            "clientes": await self._base["clientes"].delete_many(_ID_GENERADO_CLIENTE),
        }
        return {nombre: resultado.deleted_count for nombre, resultado in borrados.items()}

    async def insertar_lote(self, coleccion: str, documentos: Sequence[Mapping[str, Any]]) -> int:
        """Inserta un lote con `insert_many` (sin orden: los lotes son independientes)."""
        if not documentos:
            return 0
        resultado = await self._base[coleccion].insert_many(
            [a_entero_64(documento) for documento in documentos], ordered=False
        )
        return len(resultado.inserted_ids)

    async def contar_transacciones(self, filtro: Mapping[str, Any] | None = None) -> int:
        return await self._base["transacciones"].count_documents(dict(filtro or {}))

    async def fraude_por_patron(self) -> dict[str, int]:
        """Cuantas transacciones etiquetadas hay por patron (para el resumen y H-10)."""
        cursor = await self._base["transacciones"].aggregate(
            [
                {"$match": {"simulacion.fraude": True}},
                {"$group": {"_id": "$simulacion.patron", "total": {"$sum": 1}}},
            ]
        )
        return {fila["_id"]: fila["total"] async for fila in cursor}
