"""Alertas abiertas agrupadas por cuenta destino: el "caso" del panel (T-56).

Muchas alertas del mismo dia van a la MISMA cuenta destino (la mula) desde victimas
distintas. Agruparlas deja al agente decidir una vez con todo el contexto.

- Un caso es el conjunto de alertas ABIERTAS (`nueva`, `en_revision`) con la misma
  `cuenta_destino`. No es la coleccion `casos` (CAS-xxx, la escalacion): aquella nace al
  escalar; este es una vista calculada y no se guarda.
- La agregacion es un solo `$group` sobre `alertas`. Plan (explain, ver
  `pruebas/integracion/prueba_casos_destino_mongo.py`): `$match` por `estado` usa
  `idx_alertas_estado_puntaje_fecha`, asi que se leen solo las alertas abiertas y TODAS
  alimentan el grupo (`nReturned == totalDocsExamined`: ninguna se examina para
  descartarla). Para un solo caso (el fragmento que pide el SSE) el `$match` por
  `cuenta_destino` usa `idx_alertas_destino_estado`.
- `agrupar_por_destino` es el mismo calculo en Python puro: lo usa el doble de prueba y
  la prueba `mongo` compara los dos caminos sobre los mismos datos.
- T-82: un caso con alertas a las que el cliente respondio "No fui yo" va PRIMERO, y dentro
  del caso esas alertas van primero. El `$group` ya recorre todas las alertas abiertas, asi que
  la marca se calcula ahi mismo (un `$set` por documento) sin leer nada de mas, y el orden de
  los casos ya era un `$sort` sobre el resultado del grupo, en memoria y sobre pocos casos.
- El motor sugiere, el humano decide: nada de aqui cierra una alerta.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.modelos import COLECCION_ALERTAS, ESTADOS_ABIERTOS
from app.modelos.base import asumir_utc

LISTAS_RIESGO = "listas_riesgo"

# Alertas que se traen por caso. El total y la suma van completos; la lista se acota para
# que un destino con miles de alertas no vuelva la pagina inmanejable.
ALERTAS_POR_CASO = 50
REGLAS_POR_CASO = 3

RANGO_SEVERIDAD = {"CRITICA": 3, "ALTA": 2, "MEDIA": 1}
_SEVERIDAD_POR_RANGO = {rango: nombre for nombre, rango in RANGO_SEVERIDAD.items()}

_ABIERTAS = sorted(e.value for e in ESTADOS_ABIERTOS)

# Mismo orden que ORDEN_PANEL, dentro del caso.
_ORDEN_ALERTAS = {
    "_no_fui_yo": DESCENDING,
    "puntaje": DESCENDING,
    "fecha_creacion": DESCENDING,
    "_id": ASCENDING,
}

_CAMPOS_ALERTA = (
    "estado",
    "severidad",
    "puntaje",
    "cliente_nombre",
    "cuenta_origen",
    "cuenta_destino",
    "monto_crc",
    "fecha_creacion",
    "agente_id",
    "respuesta_cliente",
)


@dataclass
class CasoDestino:
    cuenta_destino: str
    total_alertas: int
    victimas: int
    monto_total_crc: int
    severidad_max: str
    puntaje_max: int
    mas_antigua: datetime
    mas_reciente: datetime
    reglas: list[tuple[str, int]]
    alertas: list[dict[str, Any]]
    en_lista_riesgo: bool = False
    sugerencia: str | None = field(default=None)
    # T-82: cuantas alertas del caso tienen la respuesta "No fui yo" del cliente.
    no_fui_yo: int = 0

    @property
    def alertas_ocultas(self) -> int:
        return max(0, self.total_alertas - len(self.alertas))


def rango_severidad(severidad: str) -> int:
    return RANGO_SEVERIDAD.get(severidad, 0)


def es_no_fui_yo(alerta: Mapping[str, Any]) -> bool:
    """La alerta (abierta) a la que el cliente respondio "No fui yo"."""
    return (alerta.get("respuesta_cliente") or {}).get("valor") == "no_fui_yo"


def clave_de_orden(caso: CasoDestino) -> tuple[int, int, int, datetime, str]:
    """Mas respuestas "No fui yo" primero; luego severidad maxima, puntaje maximo y la mas antigua; la cuenta desempata."""
    return (
        -caso.no_fui_yo,
        -rango_severidad(caso.severidad_max),
        -caso.puntaje_max,
        asumir_utc(caso.mas_antigua),
        caso.cuenta_destino,
    )


def reglas_mas_repetidas(
    listas: Iterable[Iterable[str]], limite: int = REGLAS_POR_CASO
) -> list[tuple[str, int]]:
    """Las reglas que mas se repiten entre las alertas: `[("R-01", 5), ...]`.

    Entra una lista de codigos por alerta. Desempata por codigo para que el resultado no
    dependa del orden en que llegaron.
    """
    cuentas: Counter[str] = Counter()
    for codigos in listas:
        cuentas.update(set(codigos))
    return sorted(cuentas.items(), key=lambda par: (-par[1], par[0]))[:limite]


def _codigos(alerta: Mapping[str, Any]) -> list[str]:
    """Codigos R-xx (v2) o REG-xxx (v1) de una alerta, igual que el `$map` del pipeline."""
    salida = []
    for regla in alerta.get("reglas_disparadas") or []:
        salida.append(regla.get("codigo") or regla.get("regla_id") if isinstance(regla, dict) else regla)
    return [c for c in salida if c]


def agrupar_por_destino(
    alertas: Iterable[Mapping[str, Any]],
    *,
    en_riesgo: frozenset[str] | set[str] = frozenset(),
    maximo_alertas: int = ALERTAS_POR_CASO,
) -> list[CasoDestino]:
    """Lo mismo que el pipeline, en Python: solo las abiertas, agrupadas y ordenadas."""
    grupos: dict[str, list[Mapping[str, Any]]] = {}
    for alerta in alertas:
        if alerta["estado"] in _ABIERTAS:
            grupos.setdefault(alerta["cuenta_destino"], []).append(alerta)

    casos = []
    for cuenta, miembros in grupos.items():
        ordenadas = sorted(miembros, key=lambda a: a["_id"])
        ordenadas.sort(key=lambda a: asumir_utc(a["fecha_creacion"]), reverse=True)
        ordenadas.sort(key=lambda a: a["puntaje"], reverse=True)
        ordenadas.sort(key=lambda a: not es_no_fui_yo(a))
        fechas = [asumir_utc(a["fecha_creacion"]) for a in miembros]
        casos.append(
            CasoDestino(
                cuenta_destino=cuenta,
                total_alertas=len(miembros),
                victimas=len({a["cliente_nombre"] for a in miembros}),
                monto_total_crc=sum(a["monto_crc"] for a in miembros),
                severidad_max=_SEVERIDAD_POR_RANGO[
                    max(rango_severidad(a["severidad"]) for a in miembros)
                ],
                puntaje_max=max(a["puntaje"] for a in miembros),
                mas_antigua=min(fechas),
                mas_reciente=max(fechas),
                reglas=reglas_mas_repetidas(_codigos(a) for a in miembros),
                alertas=[
                    {"_id": a["_id"], **{c: a[c] for c in _CAMPOS_ALERTA if c in a}}
                    for a in ordenadas[:maximo_alertas]
                ],
                en_lista_riesgo=cuenta in en_riesgo,
                no_fui_yo=sum(1 for a in miembros if es_no_fui_yo(a)),
            )
        )
    casos.sort(key=clave_de_orden)
    return casos


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

_RANGO_EXPR: dict[str, Any] = {
    "$switch": {
        "branches": [
            {"case": {"$eq": ["$severidad", nombre]}, "then": rango}
            for nombre, rango in RANGO_SEVERIDAD.items()
        ],
        "default": 0,
    }
}


def pipeline_casos(
    *,
    cuenta: str | None = None,
    limite: int = 50,
    desplazamiento: int = 0,
    maximo_alertas: int = ALERTAS_POR_CASO,
) -> list[dict[str, Any]]:
    """El `$group` de los casos. Con `cuenta`, solo ese destino (sin paginar)."""
    filtro: dict[str, Any] = {"estado": {"$in": _ABIERTAS}}
    if cuenta is not None:
        filtro["cuenta_destino"] = cuenta
    salida: dict[str, Any] = {"_id": "$_id", **{c: f"${c}" for c in _CAMPOS_ALERTA}}
    etapas: list[dict[str, Any]] = [
        {"$match": filtro},
        # T-82: 1 si el cliente respondio "No fui yo". Una sola lectura del campo por documento.
        {
            "$set": {
                "_no_fui_yo": {
                    "$cond": [{"$eq": ["$respuesta_cliente.valor", "no_fui_yo"]}, 1, 0]
                }
            }
        },
        {
            "$group": {
                "_id": "$cuenta_destino",
                "no_fui_yo": {"$sum": "$_no_fui_yo"},
                "total_alertas": {"$sum": 1},
                "clientes": {"$addToSet": "$cliente_nombre"},
                "monto_total_crc": {"$sum": "$monto_crc"},
                "puntaje_max": {"$max": "$puntaje"},
                "rango_max": {"$max": _RANGO_EXPR},
                "mas_antigua": {"$min": "$fecha_creacion"},
                "mas_reciente": {"$max": "$fecha_creacion"},
                "reglas": {
                    "$push": {
                        "$map": {
                            "input": {"$ifNull": ["$reglas_disparadas", []]},
                            "as": "r",
                            "in": {"$ifNull": ["$$r.codigo", {"$ifNull": ["$$r.regla_id", "$$r"]}]},
                        }
                    }
                },
                "alertas": {
                    "$topN": {"n": maximo_alertas, "sortBy": _ORDEN_ALERTAS, "output": salida}
                },
            }
        },
        {
            "$project": {
                "total_alertas": 1,
                "victimas": {"$size": "$clientes"},
                "monto_total_crc": 1,
                "puntaje_max": 1,
                "rango_max": 1,
                "mas_antigua": 1,
                "mas_reciente": 1,
                "reglas": 1,
                "alertas": 1,
                "no_fui_yo": 1,
            }
        },
    ]
    if cuenta is not None:
        return etapas
    etapas.append(
        {
            "$facet": {
                "pagina": [
                    {
                        "$sort": {
                            "no_fui_yo": -1,
                            "rango_max": -1,
                            "puntaje_max": -1,
                            "mas_antigua": 1,
                            "_id": 1,
                        }
                    },
                    {"$skip": desplazamiento},
                    {"$limit": limite},
                ],
                "total": [{"$count": "n"}],
            }
        }
    )
    return etapas


def _desde_documento(documento: Mapping[str, Any], en_riesgo: set[str]) -> CasoDestino:
    cuenta = documento["_id"]
    return CasoDestino(
        cuenta_destino=cuenta,
        total_alertas=documento["total_alertas"],
        victimas=documento["victimas"],
        monto_total_crc=int(documento["monto_total_crc"]),
        severidad_max=_SEVERIDAD_POR_RANGO.get(documento["rango_max"], "MEDIA"),
        puntaje_max=documento["puntaje_max"],
        mas_antigua=asumir_utc(documento["mas_antigua"]),
        mas_reciente=asumir_utc(documento["mas_reciente"]),
        reglas=reglas_mas_repetidas(documento["reglas"]),
        alertas=list(documento["alertas"]),
        en_lista_riesgo=cuenta in en_riesgo,
        no_fui_yo=int(documento.get("no_fui_yo", 0)),
    )


@runtime_checkable
class ProtocoloRepoCasosDestino(Protocol):
    async def listar(
        self, *, limite: int, desplazamiento: int = 0
    ) -> tuple[list[CasoDestino], int]: ...

    async def obtener(self, cuenta: str) -> CasoDestino | None: ...


class RepoCasosDestino:
    """Lee `alertas` (agregacion) y `listas_riesgo` (si la cuenta ya esta reportada)."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._alertas = base[COLECCION_ALERTAS]
        self._riesgo = base[LISTAS_RIESGO]

    async def _en_riesgo(self, cuentas: list[str]) -> set[str]:
        if not cuentas:
            return set()
        cursor = self._riesgo.find({"cuenta": {"$in": cuentas}, "vigente": True}, {"cuenta": 1})
        return {d["cuenta"] for d in await cursor.to_list(length=len(cuentas))}

    async def listar(
        self, *, limite: int, desplazamiento: int = 0
    ) -> tuple[list[CasoDestino], int]:
        cursor = await self._alertas.aggregate(
            pipeline_casos(limite=limite, desplazamiento=desplazamiento)
        )
        resultado = (await cursor.to_list(length=1))[0]
        documentos = resultado["pagina"]
        total = resultado["total"][0]["n"] if resultado["total"] else 0
        en_riesgo = await self._en_riesgo([d["_id"] for d in documentos])
        return [_desde_documento(d, en_riesgo) for d in documentos], total

    async def obtener(self, cuenta: str) -> CasoDestino | None:
        cursor = await self._alertas.aggregate(pipeline_casos(cuenta=cuenta))
        documentos = await cursor.to_list(length=1)
        if not documentos:
            return None
        return _desde_documento(documentos[0], await self._en_riesgo([cuenta]))
