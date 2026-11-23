"""Escritura de `listas_riesgo`: aprobar propuestas de la red de mulas y dar de baja.

Aprobar (T-51): la red de mulas (H-23) PROPONE cuentas; un supervisor o administrador
las agrega aqui. La entrada sigue el validador de `01-colecciones.js`: `_id` `RSK-nnn`,
`cuenta`, `motivo`, `fecha_reporte` (ahora, UTC), `vigente`, `esquema_version`.

- El indice `idx_listas_riesgo_cuenta` es unico: una cuenta tiene a lo sumo UNA entrada.
  Si la que hay no es vigente se reactiva (motivo y fecha nuevos) en vez de duplicarla.
- Idempotente: si la cuenta ya tiene una entrada vigente no se escribe otra; el
  resultado dice `creada: False` y devuelve la que ya estaba. La busqueda y la insercion
  son un solo `update_one` con `upsert` y `$setOnInsert`, de modo que dos aprobaciones
  seguidas no duplican.
- El `_id` es `RSK-` mas el siguiente numero de tres cifras. Las entradas que fabrica el
  generador usan cuatro cifras (`RSK-0001`) y el generador las borra al regenerar: las de
  tres cifras no se tocan.

Dar de baja (D-41): `delete_one` real, porque el enunciado exige eliminacion. El caso de
negocio es una cuenta reportada por error. Antes de borrar se deja rastro en el log
`centinela.auditoria` con el documento completo y quien lo borro. NO se crea una
coleccion de auditoria: el contrato fija 13 colecciones, y ninguna de las existentes es
una bitacora de este tipo (`alertas.historial` es de las alertas; `deteccion_estado`
guarda el token del motor). El rastro queda en el log, que es lo que hay.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

LISTAS_RIESGO = "listas_riesgo"
ESQUEMA_VERSION_LISTAS = 1
_ID_TRES_CIFRAS = re.compile(r"^RSK-(\d{3})$")
INTENTOS_ID = 5

registro_auditoria = logging.getLogger("centinela.auditoria")


class EntradaNoEncontrada(LookupError):
    """No existe la entrada de `listas_riesgo` que se quiere dar de baja."""


@dataclass(frozen=True)
class ResultadoAprobacion:
    entrada: dict[str, Any]
    creada: bool  # False: ya estaba vigente y no se escribio nada


def motivo_de_red(cuenta_partida: str) -> str:
    return f"red de mulas desde {cuenta_partida}"


class RepoListasRiesgo:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def _siguiente_id(self) -> str:
        ultimo = await self._base[LISTAS_RIESGO].find_one(
            {"_id": {"$regex": r"^RSK-[0-9]{3}$"}}, {"_id": 1}, sort=[("_id", -1)]
        )
        numero = int(_ID_TRES_CIFRAS.match(ultimo["_id"]).group(1)) if ultimo else 0  # type: ignore[union-attr]
        return f"RSK-{numero + 1:03d}"

    async def vigente_de(self, cuenta: str) -> dict[str, Any] | None:
        return await self._base[LISTAS_RIESGO].find_one({"cuenta": cuenta, "vigente": True})

    async def entradas_de(self, cuenta: str) -> list[dict[str, Any]]:
        cursor = self._base[LISTAS_RIESGO].find({"cuenta": cuenta}).sort("_id", 1)
        return await cursor.to_list(length=None)

    async def entradas_de_cuentas(self, cuentas: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Las entradas de varias cuentas de una vez, para pintar los botones de baja."""
        por_cuenta: dict[str, list[dict[str, Any]]] = {}
        async for entrada in self._base[LISTAS_RIESGO].find({"cuenta": {"$in": cuentas}}).sort(
            "_id", 1
        ):
            por_cuenta.setdefault(entrada["cuenta"], []).append(entrada)
        return por_cuenta

    async def aprobar(
        self, cuenta: str, *, cuenta_partida: str, agente_id: str, ahora: datetime | None = None
    ) -> ResultadoAprobacion:
        """Agrega `cuenta` como vigente si no lo esta. Idempotente."""
        ahora = ahora or datetime.now(UTC)
        motivo = motivo_de_red(cuenta_partida)
        for _ in range(INTENTOS_ID):
            nuevo_id = await self._siguiente_id()
            documento = {
                "_id": nuevo_id,
                "cuenta": cuenta,
                "motivo": motivo,
                "fecha_reporte": ahora,
                "vigente": True,
                "esquema_version": ESQUEMA_VERSION_LISTAS,
            }
            # `cuenta` y `vigente` ya los pone el filtro del upsert.
            al_insertar = {k: v for k, v in documento.items() if k not in ("cuenta", "vigente")}
            try:
                resultado = await self._base[LISTAS_RIESGO].update_one(
                    {"cuenta": cuenta, "vigente": True},
                    {"$setOnInsert": al_insertar},
                    upsert=True,
                )
            except DuplicateKeyError:
                # `idx_listas_riesgo_cuenta` es unico: o la cuenta ya tiene una entrada
                # (vigente por una carrera, o dada de baja logica) o se tomo el mismo _id.
                existente = await self._base[LISTAS_RIESGO].find_one({"cuenta": cuenta})
                if existente is None:
                    continue  # era el _id: se pide el siguiente
                if existente["vigente"]:
                    return ResultadoAprobacion(existente, creada=False)
                reactivada = {
                    **existente,
                    "motivo": motivo,
                    "fecha_reporte": ahora,
                    "vigente": True,
                }
                await self._base[LISTAS_RIESGO].update_one(
                    {"_id": existente["_id"]},
                    {"$set": {"motivo": motivo, "fecha_reporte": ahora, "vigente": True}},
                )
                registro_auditoria.info(
                    "lista_riesgo_reactivada id=%s cuenta=%s motivo=%r aprobo=%s",
                    existente["_id"], cuenta, motivo, agente_id,
                )
                return ResultadoAprobacion(reactivada, creada=True)
            if resultado.upserted_id is None:
                existente = await self.vigente_de(cuenta)
                return ResultadoAprobacion(existente or documento, creada=False)
            registro_auditoria.info(
                "lista_riesgo_alta id=%s cuenta=%s motivo=%r aprobo=%s",
                nuevo_id,
                cuenta,
                motivo,
                agente_id,
            )
            return ResultadoAprobacion(documento, creada=True)
        raise RuntimeError("no se pudo asignar un identificador RSK libre")

    async def dar_de_baja(self, riesgo_id: str, *, agente_id: str) -> dict[str, Any]:
        """Borra la entrada (`delete_one`) dejando antes el rastro en el log."""
        entrada = await self._base[LISTAS_RIESGO].find_one({"_id": riesgo_id})
        if entrada is None:
            raise EntradaNoEncontrada(f"No existe la entrada {riesgo_id}")
        registro_auditoria.warning(
            "lista_riesgo_baja id=%s cuenta=%s motivo=%r fecha_reporte=%s vigente=%s dio_de_baja=%s",
            entrada["_id"],
            entrada["cuenta"],
            entrada["motivo"],
            entrada["fecha_reporte"].isoformat(),
            entrada["vigente"],
            agente_id,
        )
        resultado = await self._base[LISTAS_RIESGO].delete_one({"_id": riesgo_id})
        if resultado.deleted_count == 0:  # otro la borro entre la lectura y el borrado
            raise EntradaNoEncontrada(f"No existe la entrada {riesgo_id}")
        return entrada
