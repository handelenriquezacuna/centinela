"""Consultas sobre `reglas_deteccion` y la politica vigente (H-16).

Versionado de una regla, en un solo documento: cada edicion sube `version` en uno y
empuja la version anterior completa a `versiones_anteriores`. No hay coleccion nueva
(el contrato es de 13) y no hace falta: las reglas se editan poco, asi que el arreglo
crece acotado, y la regla se lee siempre junto con su historia.

La edicion es UNA sola operacion (`find_one_and_update`) filtrada por la version que
se leyo. Es concurrencia optimista: si otro supervisor edito en el medio, el filtro no
encuentra nada, no se escribe nada y la ruta responde 409. Sin ese filtro, la segunda
edicion pisaria la primera y su version anterior quedaria mal registrada.

`instantanea` y `siguiente_regla_id` son funciones puras para que el doble de
`pruebas/dobles/` use exactamente la misma regla que el repositorio real.

T-19: `leer_politica_vigente` es la UNICA lectura de `politica_deteccion` del sistema.
La usan la API de reglas (simular) y D1 (`app/repos/deteccion.py`); antes cada uno
tenia su propio `find_one` y su propia forma de devolverla (un dict aca, el modelo
alla). `pruebas/arquitectura/prueba_politica_unica.py` impide que vuelva a duplicarse.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pymongo import ASCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.deteccion.evaluador import PoliticaDeteccion
from app.modelos import COLECCION_POLITICA
from app.repos.carga_demo import a_entero_64

COLECCION_REGLAS = "reglas_deteccion"
PREFIJO_REGLA = "REG-"

# Campos que el historial NO copia: el `_id` es el de la regla misma y anidar la
# historia dentro de la historia la haria crecer al cuadrado.
_FUERA_DE_LA_INSTANTANEA = frozenset({"_id", "versiones_anteriores"})


# La politica vigente es la unica con `vigente: true` (lo sostiene el indice de
# 02-indices.js). Es la consulta que fija docs/06-plataforma.md, 1.5.
FILTRO_POLITICA_VIGENTE: dict[str, Any] = {"vigente": True}


async def leer_politica_vigente(base: AsyncDatabase) -> PoliticaDeteccion | None:
    """La politica vigente, ya como modelo, o None si no hay ninguna."""
    documento = await base[COLECCION_POLITICA].find_one(FILTRO_POLITICA_VIGENTE)
    return PoliticaDeteccion.model_validate(documento) if documento is not None else None


class CodigoDuplicado(Exception):
    """Ya hay una regla con ese codigo corto (R-01). Lo sostiene `idx_reglas_codigo`."""


def instantanea(documento: Mapping[str, Any], reemplazada_en: datetime) -> dict[str, Any]:
    """La version vigente de la regla tal como queda guardada en su historia."""
    copia = {
        clave: valor
        for clave, valor in documento.items()
        if clave not in _FUERA_DE_LA_INSTANTANEA
    }
    copia["reemplazada_en"] = reemplazada_en
    return copia


def siguiente_regla_id(existentes: list[str]) -> str:
    """REG-NNN siguiente al mayor que existe. El validador fija tres digitos."""
    numeros = [
        int(regla_id.removeprefix(PREFIJO_REGLA))
        for regla_id in existentes
        if regla_id.startswith(PREFIJO_REGLA)
    ]
    return f"{PREFIJO_REGLA}{max(numeros, default=0) + 1:03d}"


@runtime_checkable
class ProtocoloRepoReglas(Protocol):
    """Lo que la API de reglas necesita. El doble de pruebas lo implementa igual."""

    async def listar(self, *, activa: bool | None) -> list[dict[str, Any]]: ...

    async def obtener(self, regla_id: str) -> dict[str, Any] | None: ...

    async def crear(self, documento: dict[str, Any]) -> dict[str, Any]: ...

    async def editar(
        self,
        actual: dict[str, Any],
        cambios: dict[str, Any],
        ahora: datetime,
    ) -> dict[str, Any] | None: ...

    async def politica_vigente(self) -> PoliticaDeteccion | None: ...


class RepoReglas:
    """Acceso a `reglas_deteccion`; la politica vigente sale de `leer_politica_vigente`."""

    # Reintentos si dos altas simultaneas calculan el mismo REG-NNN.
    _INTENTOS_ALTA = 3

    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base
        self._reglas = base[COLECCION_REGLAS]

    async def listar(self, *, activa: bool | None = None) -> list[dict[str, Any]]:
        filtro: dict[str, Any] = {} if activa is None else {"activa": activa}
        cursor = self._reglas.find(filtro).sort([("codigo", ASCENDING)])
        return await cursor.to_list(length=None)

    async def obtener(self, regla_id: str) -> dict[str, Any] | None:
        return await self._reglas.find_one({"_id": regla_id})

    async def crear(self, documento: dict[str, Any]) -> dict[str, Any]:
        """Inserta con el siguiente REG-NNN. `documento` llega sin `_id`.

        El codigo duplicado se revisa antes para no depender de que el indice unico
        exista (lo crea `02-indices.js`), y el `DuplicateKeyError` se atiende igual
        por si dos altas compiten.
        """
        if await self._reglas.find_one({"codigo": documento["codigo"]}, {"_id": 1}):
            raise CodigoDuplicado(documento["codigo"])

        for _ in range(self._INTENTOS_ALTA):
            existentes = await self._reglas.distinct("_id")
            nuevo = {"_id": siguiente_regla_id(existentes), **documento}
            try:
                await self._reglas.insert_one(a_entero_64(nuevo))
            except DuplicateKeyError as fallo:
                if "codigo" in (fallo.details or {}).get("keyValue", {}):
                    raise CodigoDuplicado(documento["codigo"]) from fallo
                continue
            return nuevo
        raise CodigoDuplicado(documento["codigo"])  # pragma: no cover - carrera triple

    async def editar(
        self,
        actual: dict[str, Any],
        cambios: dict[str, Any],
        ahora: datetime,
    ) -> dict[str, Any] | None:
        """Aplica `cambios` como version siguiente. `None` si la version ya cambio."""
        version_leida = actual.get("version")
        return await self._reglas.find_one_and_update(
            {"_id": actual["_id"], "version": version_leida},
            {
                "$set": a_entero_64({**cambios, "version": (version_leida or 0) + 1}),
                "$push": {"versiones_anteriores": a_entero_64(instantanea(actual, ahora))},
            },
            return_document=ReturnDocument.AFTER,
        )

    async def politica_vigente(self) -> PoliticaDeteccion | None:
        return await leer_politica_vigente(self._base)
