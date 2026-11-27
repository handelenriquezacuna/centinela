"""Agentes como usuarios del sistema: leer su credencial y guardar una nueva (H-25).

Es el unico lugar que escribe `agentes.credencial`. La contrasena nunca llega aqui: se
recibe ya cifrada (`app/seguridad/contrasenas.py`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.seguridad.bloqueo import ResultadoFallo, calcular_fallo

COLECCION_AGENTES = "agentes"


@runtime_checkable
class ProtocoloRepoAgentes(Protocol):
    async def obtener(self, agente_id: str) -> dict[str, Any] | None: ...

    async def guardar_credencial(
        self, agente_id: str, credencial: dict[str, Any], rol: str | None = None
    ) -> bool: ...

    async def registrar_fallo(
        self, agente_id: str, *, maximo: int, bloqueo_minutos: int, ahora: datetime
    ) -> ResultadoFallo | None: ...

    async def reiniciar_intentos(self, agente_id: str) -> bool: ...

    async def desbloquear(self, agente_id: str) -> bool: ...

    async def listar(self) -> list[dict[str, Any]]: ...


def _vigente(ahora: datetime) -> dict[str, Any]:
    """Expresion de pipeline: la cuenta tiene un bloqueo que todavia no vence."""
    return {"$gt": ["$bloqueado_hasta", ahora]}


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

    async def registrar_fallo(
        self, agente_id: str, *, maximo: int, bloqueo_minutos: int, ahora: datetime
    ) -> ResultadoFallo | None:
        """Cuenta un intento fallido, en UNA operacion atomica (sin carreras).

        Es `find_one_and_update` con pipeline: la lectura del contador, la decision y la
        escritura ocurren dentro del servidor, asi que N intentos concurrentes suman
        exactamente N y el bloqueo se fija una sola vez. La regla es la de
        `bloqueo.calcular_fallo`:

        - bloqueo vigente: no cambia nada (intentar no lo alarga);
        - bloqueo vencido: el conteo vuelve a 1;
        - al llegar a `maximo`: `bloqueado_hasta = ahora + bloqueo_minutos`.

        `None` si la cuenta no existe. Un fallo no escribe nada que no sea del agente.
        """
        nuevo_hasta = ahora + timedelta(minutes=bloqueo_minutos)
        vigente = _vigente(ahora)
        vencido = {
            "$and": [
                {"$eq": [{"$type": "$bloqueado_hasta"}, "date"]},
                {"$lte": ["$bloqueado_hasta", ahora]},
            ]
        }
        pipeline: list[dict[str, Any]] = [
            {
                "$set": {
                    "intentos_fallidos": {
                        "$cond": [
                            vigente,
                            {"$ifNull": ["$intentos_fallidos", 0]},
                            {
                                "$cond": [
                                    vencido,
                                    1,
                                    {"$add": [{"$ifNull": ["$intentos_fallidos", 0]}, 1]},
                                ]
                            },
                        ]
                    }
                }
            },
            {
                "$set": {
                    "bloqueado_hasta": {
                        "$cond": [
                            vigente,
                            "$bloqueado_hasta",
                            {
                                "$cond": [
                                    {"$gte": ["$intentos_fallidos", maximo]},
                                    nuevo_hasta,
                                    "$$REMOVE",
                                ]
                            },
                        ]
                    }
                }
            },
        ]
        antes = await self._base[COLECCION_AGENTES].find_one_and_update(
            {"_id": agente_id}, pipeline, return_document=ReturnDocument.BEFORE
        )
        if antes is None:
            return None
        # El documento de ANTES es el que esta actualizacion leyo, atomicamente: aplicarle la
        # regla en Python da exactamente lo que el pipeline escribio, y dice sin ambiguedad
        # (ni choques de milisegundo entre intentos concurrentes) cual intento causo el bloqueo.
        return calcular_fallo(antes, maximo=maximo, bloqueo_minutos=bloqueo_minutos, ahora=ahora)

    async def reiniciar_intentos(self, agente_id: str) -> bool:
        """Un login correcto: contador a cero y sin bloqueo. True si habia algo que limpiar."""
        resultado = await self._base[COLECCION_AGENTES].update_one(
            {
                "_id": agente_id,
                "$or": [
                    {"intentos_fallidos": {"$exists": True}},
                    {"bloqueado_hasta": {"$exists": True}},
                ],
            },
            {"$unset": {"intentos_fallidos": "", "bloqueado_hasta": ""}},
        )
        return resultado.modified_count == 1

    async def desbloquear(self, agente_id: str) -> bool:
        """Quita el bloqueo y el contador (administrador). False si no existe el agente."""
        resultado = await self._base[COLECCION_AGENTES].update_one(
            {"_id": agente_id}, {"$unset": {"intentos_fallidos": "", "bloqueado_hasta": ""}}
        )
        return resultado.matched_count == 1

    async def listar(self) -> list[dict[str, Any]]:
        """Los agentes con lo que el administrador necesita; el hash no sale del repositorio."""
        cursor = await self._base[COLECCION_AGENTES].aggregate(
            [
                {
                    "$project": {
                        "nombre": 1,
                        "rol": 1,
                        "activo": 1,
                        "intentos_fallidos": 1,
                        "bloqueado_hasta": 1,
                        "tiene_credencial": {"$ne": [{"$type": "$credencial"}, "missing"]},
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
        return [dict(d) async for d in cursor]
