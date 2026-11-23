"""H-15 · Busqueda forense sobre `transacciones`.

`construir_filtro_busqueda` y `problemas_del_filtro` son funciones puras (se prueban sin
base y las usa el doble de `pruebas/dobles/`). `RepoBusqueda` es la unica que habla con
Mongo.

**Que indice sirve a cada filtro** (`scripts/semilla/02-indices.js`):

- `cuenta`: tres ramas de un `$or` (`cuenta_origen`, `sinpe_origen`, `cuenta_destino`),
  cada una con su indice (`idx_transacciones_cuenta_origen_fecha`,
  `idx_transacciones_sinpe_origen_fecha`, `idx_transacciones_cuenta_destino`).
- `monto_min` / `monto_max`: `idx_transacciones_monto_fecha` (`$gte` / `$lte`).
- `estado_alerta`: consulta previa a `alertas` por estado (`idx_alertas_estado_severidad_fecha`)
  y `_id: {$in: [...]}` sobre `transacciones`. Sin `$lookup` por documento.
- `desde` / `hasta` solos, `canal` solo, o sin filtro: NO tienen indice que los sirva
  (no hay indice con `fecha` o `canal` como prefijo). Hacen COLLSCAN y ordenan en memoria;
  es el hueco que se reporta como duda en el informe de H-15.

**Alerta asociada**: se resuelve para la pagina, no por documento: una sola consulta a
`alertas` con `transaccion_id: {$in: [ids de la pagina]}`, que la sirve el indice unico
`idx_alertas_transaccion_id`. Una transaccion tiene a lo sumo una alerta.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.modelos import COLECCION_ALERTAS, EstadoAlerta

COLECCION_TRANSACCIONES = "transacciones"

# Mismo criterio que el panel: lo mas reciente primero, el identificador desempata para
# que paginar no repita ni se salte documentos.
ORDEN_BUSQUEDA: list[tuple[str, int]] = [("fecha", DESCENDING), ("_id", ASCENDING)]

# Tope de identificadores que una busqueda por estado de alerta lleva en el `$in`. Una
# lista sin tope en una base con millones de alertas excederia los 16 MiB del comando.
MAX_IDS_ESTADO_ALERTA = 50_000

# Lo que se exporta a CSV como maximo: el CSV sale del servidor y no es un volcado.
LIMITE_EXPORTACION = 10_000

PATRON_CUENTA = re.compile(r"^[A-Za-z0-9-]{1,32}$")


@dataclass(frozen=True)
class FiltroBusqueda:
    """Los filtros de H-15, todos opcionales y combinables (AND)."""

    desde: datetime | None = None
    hasta: datetime | None = None
    monto_min: int | None = None
    monto_max: int | None = None
    cuenta: str | None = None
    canales: tuple[str, ...] = ()
    estado_alerta: EstadoAlerta | None = None


@dataclass
class ResultadoBusqueda:
    """Una pagina: documentos con su alerta asociada, total del filtro y plan opcional."""

    documentos: list[dict[str, Any]]
    total: int
    plan: dict[str, Any] | None = None
    # Solo para el doble y las pruebas: el filtro de Mongo que se uso.
    filtro_mongo: dict[str, Any] = field(default_factory=dict)


def problemas_del_filtro(filtro: FiltroBusqueda) -> list[tuple[str, str]]:
    """Pares (campo, mensaje) de lo que esta mal en el filtro. Vacio si es valido."""
    problemas: list[tuple[str, str]] = []
    for campo in ("monto_min", "monto_max"):
        valor = getattr(filtro, campo)
        if valor is not None and valor < 0:
            problemas.append((campo, "debe ser mayor o igual a 0"))
    if (
        filtro.monto_min is not None
        and filtro.monto_max is not None
        and filtro.monto_min > filtro.monto_max
    ):
        problemas.append(("monto_min", "no puede ser mayor que monto_max"))
    if (
        filtro.desde is not None
        and filtro.hasta is not None
        and filtro.desde > filtro.hasta
    ):
        problemas.append(("desde", "no puede ser posterior a hasta"))
    if filtro.cuenta is not None and not PATRON_CUENTA.match(filtro.cuenta):
        problemas.append(
            ("cuenta", "solo letras, dígitos y guiones, hasta 32 caracteres")
        )
    return problemas


def construir_filtro_busqueda(
    filtro: FiltroBusqueda, ids_por_estado: Sequence[str] | None = None
) -> dict[str, Any]:
    """Filtro de Mongo para `transacciones`. Cada filtro que llega agrega una condicion.

    `ids_por_estado` son los `transaccion_id` de las alertas en el estado pedido
    (resueltos antes, en `alertas`); `None` significa "sin filtro por estado".
    """
    condiciones: dict[str, Any] = {}

    rango_fecha: dict[str, datetime] = {}
    if filtro.desde is not None:
        rango_fecha["$gte"] = filtro.desde
    if filtro.hasta is not None:
        rango_fecha["$lte"] = filtro.hasta
    if rango_fecha:
        condiciones["fecha"] = rango_fecha

    rango_monto: dict[str, int] = {}
    if filtro.monto_min is not None:
        rango_monto["$gte"] = filtro.monto_min
    if filtro.monto_max is not None:
        rango_monto["$lte"] = filtro.monto_max
    if rango_monto:
        condiciones["monto_crc"] = rango_monto

    if filtro.canales:
        condiciones["canal"] = {"$in": list(filtro.canales)}

    if ids_por_estado is not None:
        condiciones["_id"] = {"$in": list(ids_por_estado)}

    if filtro.cuenta is not None:
        # Una sola cuenta puede ser CTA-xxx (origen propio) o un numero SINPE (origen
        # propio o destino). Cada rama usa su indice.
        condiciones["$or"] = [
            {"cuenta_origen": filtro.cuenta},
            {"sinpe_origen": filtro.cuenta},
            {"cuenta_destino": filtro.cuenta},
        ]
    return condiciones


def resumir_plan(explicacion: Mapping[str, Any]) -> dict[str, Any]:
    """El `explain` en lo que le importa a quien depura: etapas, indices y conteos."""
    estadisticas = explicacion.get("executionStats", {})
    ganador = explicacion.get("queryPlanner", {}).get("winningPlan", {})
    etapas: list[str] = []
    indices: list[str] = []

    def recorrer(nodo: Any) -> None:
        if isinstance(nodo, Mapping):
            if "stage" in nodo:
                etapas.append(str(nodo["stage"]))
            if "indexName" in nodo and nodo["indexName"] not in indices:
                indices.append(str(nodo["indexName"]))
            for valor in nodo.values():
                recorrer(valor)
        elif isinstance(nodo, list):
            for valor in nodo:
                recorrer(valor)

    recorrer(ganador)
    return {
        "etapas": etapas,
        "indices": indices,
        "documentos_examinados": estadisticas.get("totalDocsExamined"),
        "claves_examinadas": estadisticas.get("totalKeysExamined"),
        "devueltos": estadisticas.get("nReturned"),
        "milisegundos": estadisticas.get("executionTimeMillis"),
        "plan_ganador": dict(ganador),
    }


@runtime_checkable
class ProtocoloRepoBusqueda(Protocol):
    async def buscar(
        self,
        filtro: FiltroBusqueda,
        *,
        limite: int,
        desplazamiento: int = 0,
        depurar: bool = False,
    ) -> ResultadoBusqueda: ...


class RepoBusqueda:
    """Acceso de lectura a `transacciones` y a la alerta asociada en `alertas`."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._transacciones = base[COLECCION_TRANSACCIONES]
        self._alertas = base[COLECCION_ALERTAS]
        self._base = base

    async def _ids_por_estado(self, estado: EstadoAlerta) -> list[str]:
        cursor = self._alertas.find(
            {"estado": EstadoAlerta(estado).value},
            {"transaccion_id": 1},
            limit=MAX_IDS_ESTADO_ALERTA,
        )
        return [d["transaccion_id"] async for d in cursor]

    async def buscar(
        self,
        filtro: FiltroBusqueda,
        *,
        limite: int,
        desplazamiento: int = 0,
        depurar: bool = False,
    ) -> ResultadoBusqueda:
        ids = (
            await self._ids_por_estado(filtro.estado_alerta)
            if filtro.estado_alerta is not None
            else None
        )
        consulta = construir_filtro_busqueda(filtro, ids)

        total = await self._transacciones.count_documents(consulta)
        cursor = (
            self._transacciones.find(consulta, {"simulacion": 0})
            .sort(ORDEN_BUSQUEDA)
            .skip(desplazamiento)
            .limit(limite)
        )
        documentos = [documento async for documento in cursor]

        # La alerta asociada de la pagina, en una consulta: sin `$lookup` por documento.
        if documentos:
            por_transaccion = {
                a["transaccion_id"]: a
                async for a in self._alertas.find(
                    {"transaccion_id": {"$in": [d["_id"] for d in documentos]}},
                    {"estado": 1, "transaccion_id": 1},
                )
            }
            for documento in documentos:
                alerta = por_transaccion.get(documento["_id"])
                documento["alerta_id"] = alerta["_id"] if alerta else None
                documento["alerta_estado"] = alerta["estado"] if alerta else None

        plan = None
        if depurar:
            explicacion = await self._base.command(
                {
                    "explain": {
                        "find": COLECCION_TRANSACCIONES,
                        "filter": consulta,
                        "sort": dict(ORDEN_BUSQUEDA),
                        "skip": desplazamiento,
                        "limit": limite,
                    },
                    "verbosity": "executionStats",
                }
            )
            plan = resumir_plan(explicacion)
        return ResultadoBusqueda(documentos, total, plan, consulta)
