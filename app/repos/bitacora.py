"""Bitacora de auditoria (T-84, D-83): lo que cada coleccion ya registra, en una sola lista.

NO hay coleccion nueva (el contrato fija 13). La bitacora es una CONSULTA: un aggregation
pipeline que parte de `alertas` y une con `$unionWith` las demas fuentes, normalizando cada
una a la misma forma `{fecha, actor, accion, objeto, detalle, origen}`:

| origen            | de donde sale                          | accion                              | actor         |
|-------------------|----------------------------------------|-------------------------------------|---------------|
| alertas           | `alertas.historial` (`$unwind`)        | `alerta_<estado>` o la accion cruda | `agente_id`   |
| reglas_deteccion  | `versiones_anteriores` (par vN -> vN+1)| `regla_editada`                     | no se guarda  |
| politica_deteccion| `versiones_anteriores`                 | `politica_editada`                  | no se guarda  |
| listas_riesgo     | `fecha_reporte`                        | `lista_riesgo_alta`                 | no se guarda  |
| casos             | `fecha_apertura`                       | `caso_abierto`                      | `agente_id` solo en lote |
| notificaciones    | `fecha`                                | `notificacion_<estado_envio>`       | sistema (nulo)|
| respaldos         | manifiestos en disco (Python)          | `respaldo_creado`/`respaldo_verificado` | creado_por / verificado_por |

`actor` nulo significa "no registrado" o "automatico"; la columna `origen` lo aclara. No se
inventa un actor que la base no guarda. Se omiten: la creacion de la alerta por el motor
(`historial` con accion `nueva` y sin agente: una fila por alerta ahogaria la bitacora y la
alerta misma ya la registra), las bajas de `listas_riesgo` (`delete_one` real: solo queda en el
log `centinela.auditoria`) y las credenciales de `agentes` (no guardan fecha).

Costo: es una consulta de administracion, no de la cola. Cada rama filtra por fecha, actor y
objeto ANTES de `$unwind` cuando puede (indices en docs/06, T-84); despues del `$unionWith`
no hay indice que valga, asi que el orden y el total se calculan sobre lo ya filtrado, con
`$facet` (una sola pasada) y `$sort` + `$limit` como top-k acotado.

Tolera, sin depender de ellos, los campos de ramas paralelas: una accion de historial que no
es un estado (`respuesta_cliente`, T-82) pasa tal cual, y `agente_id` en versiones, listas o
casos se lee si existe.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pymongo.asynchronous.database import AsyncDatabase

ESTADOS_ALERTA = ("nueva", "en_revision", "confirmada", "descartada", "escalada")
PATRON_OBJETO = re.compile(r"^[A-Za-z0-9-]{1,32}$")
PATRON_ACTOR = re.compile(r"^[A-Za-z0-9-]{1,32}$")
PATRON_ACCION = re.compile(r"^[a-z_]{1,40}$")
TOPE_MAXIMO = 10_100  # desplazamiento (10.000) + una pagina acotada

# Campos comparados para decir "que cambio" entre dos versiones. Los escalares muestran el
# valor; los estructurados solo dicen que cambiaron.
CAMPOS_REGLA = (("umbral", "peso", "activa"), ("descripcion", "tipo", "parametros"))
CAMPOS_POLITICA = (("umbral_alerta", "puntaje_maximo"), ("cortes_severidad", "notificacion"))

ACCIONES_CONOCIDAS = (
    "alerta_nueva",
    "alerta_en_revision",
    "alerta_confirmada",
    "alerta_descartada",
    "alerta_escalada",
    "respuesta_cliente",
    "regla_editada",
    "politica_editada",
    "lista_riesgo_alta",
    "caso_abierto",
    "notificacion_pendiente",
    "notificacion_enviado",
    "notificacion_fallido",
    "respaldo_creado",
    "respaldo_verificado",
)


@dataclass(frozen=True)
class FiltroBitacora:
    desde: datetime | None = None
    hasta: datetime | None = None
    actor: str | None = None
    acciones: tuple[str, ...] = ()
    objeto: str | None = None  # prefijo: ALR-, REG-000, 6033-9001

    def problemas(self) -> list[tuple[str, str]]:
        malos: list[tuple[str, str]] = []
        if self.desde is not None and self.hasta is not None and self.desde > self.hasta:
            malos.append(("desde", "no puede ser posterior a hasta"))
        if self.actor is not None and not PATRON_ACTOR.match(self.actor):
            malos.append(("actor", "solo letras, digitos y guiones, hasta 32 caracteres"))
        if self.objeto is not None and not PATRON_OBJETO.match(self.objeto):
            malos.append(("objeto", "solo letras, digitos y guiones, hasta 32 caracteres"))
        for accion in self.acciones:
            if not PATRON_ACCION.match(accion):
                malos.append(("accion", f"'{accion}' no es un tipo de accion valido"))
        return malos


def _rango(filtro: FiltroBitacora) -> dict[str, Any] | None:
    rango: dict[str, Any] = {}
    if filtro.desde is not None:
        rango["$gte"] = filtro.desde
    if filtro.hasta is not None:
        rango["$lte"] = filtro.hasta
    return rango or None


def _prefijo(filtro: FiltroBitacora) -> dict[str, Any] | None:
    # El texto del usuario se escapa: es un prefijo literal, nunca un patron.
    if filtro.objeto is None:
        return None
    # Solo los metacaracteres: el guion queda literal para que Mongo siga usando el
    # prefijo como limite del indice.
    return {"$regex": "^" + re.sub(r"([.^$*+?{}\[\]\\|()])", r"\\\1", filtro.objeto)}


def _condiciones(pares: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    return {campo: valor for campo, valor in pares if valor is not None}


def _etapa_accion(filtro: FiltroBitacora) -> list[dict[str, Any]]:
    return [{"$match": {"accion": {"$in": list(filtro.acciones)}}}] if filtro.acciones else []


def _coincidir(condiciones: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"$match": condiciones}] if condiciones else []


# -- Ramas ---------------------------------------------------------------------------------


def rama_alertas(filtro: FiltroBitacora) -> list[dict[str, Any]]:
    """`alertas.historial`: una fila por accion humana (o respuesta del cliente)."""
    rango = _rango(filtro)
    # Poda ANTES del $unwind: solo las alertas con alguna accion en el rango / del actor.
    # `$elemMatch`: la fecha y el actor sobre la MISMA accion, con limites ajustados en los
    # indices multillave (sin el, `$gte` y `$lte` se resolverian por separado).
    en_la_accion = _condiciones([("fecha", rango), ("agente_id", filtro.actor)])
    poda = _condiciones(
        [
            ("_id", _prefijo(filtro)),
            ("historial", {"$elemMatch": en_la_accion} if en_la_accion else None),
        ]
    )
    exacto = _condiciones([("historial.fecha", rango), ("historial.agente_id", filtro.actor)])
    exacto["$or"] = [
        {"historial.accion": {"$ne": "nueva"}},
        {"historial.agente_id": {"$ne": None}},
    ]
    es_estado = {"$in": ["$historial.accion", list(ESTADOS_ALERTA)]}
    anteriores = {
        "$filter": {
            "input": {"$slice": ["$historial_completo", 0, "$indice"]},
            "as": "h",
            "cond": {"$in": ["$$h.accion", list(ESTADOS_ALERTA)]},
        }
    }
    nota = {"$ifNull": ["$historial.nota", ""]}
    return [
        *_coincidir(poda),
        {"$set": {"historial_completo": "$historial"}},
        {"$unwind": {"path": "$historial", "includeArrayIndex": "indice"}},
        {"$match": exacto},
        {
            "$set": {
                "anterior": {
                    "$cond": [
                        {"$gt": ["$indice", 0]},
                        {
                            "$arrayElemAt": [
                                {"$map": {"input": anteriores, "as": "h", "in": "$$h.accion"}},
                                -1,
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "fecha": "$historial.fecha",
                "actor": {"$ifNull": ["$historial.agente_id", None]},
                "accion": {
                    "$cond": [es_estado, {"$concat": ["alerta_", "$historial.accion"]}, "$historial.accion"]
                },
                "objeto": "$_id",
                "detalle": {
                    "$cond": [
                        es_estado,
                        {
                            "$concat": [
                                {"$ifNull": ["$anterior", "?"]},
                                " -> ",
                                "$historial.accion",
                                {"$cond": [{"$eq": [nota, ""]}, "", {"$concat": [": ", nota]}]},
                            ]
                        },
                        nota,
                    ]
                },
                "origen": {"$literal": "alertas"},
            }
        },
        *_etapa_accion(filtro),
    ]


def _cambios(campos: tuple[tuple[str, ...], tuple[str, ...]]) -> dict[str, Any]:
    """Expresion que lista, de `$$a` a `$$b`, que campos cambiaron."""
    escalares, estructurados = campos
    frases: list[dict[str, Any]] = []
    for campo in escalares:
        antes, despues = f"$$a.{campo}", f"$$b.{campo}"
        frases.append(
            {
                "$cond": [
                    {"$ne": [antes, despues]},
                    {
                        "$concat": [
                            campo,
                            " ",
                            {"$ifNull": [{"$toString": antes}, "-"]},
                            " -> ",
                            {"$ifNull": [{"$toString": despues}, "-"]},
                        ]
                    },
                    None,
                ]
            }
        )
    for campo in estructurados:
        frases.append(
            {"$cond": [{"$ne": [f"$$a.{campo}", f"$$b.{campo}"]}, f"{campo} cambio", None]}
        )
    unidas = {
        "$reduce": {
            "input": {"$filter": {"input": frases, "as": "f", "cond": {"$ne": ["$$f", None]}}},
            "initialValue": "",
            "in": {
                "$concat": ["$$value", {"$cond": [{"$eq": ["$$value", ""]}, "", ", "]}, "$$this"]
            },
        }
    }
    return {"$cond": [{"$eq": [unidas, ""]}, "sin cambios en los campos comparados", unidas]}


def rama_versiones(
    filtro: FiltroBitacora,
    *,
    origen: str,
    accion: str,
    campos: tuple[tuple[str, ...], tuple[str, ...]],
    rotulo: str,
) -> list[dict[str, Any]]:
    """Ediciones de una regla o de la politica: cada par de versiones consecutivas es un evento.

    `versiones_anteriores` guarda lo que RIGIO, con `reemplazada_en` (el momento de la
    edicion); el que reemplazo a la ultima es el documento actual. El detalle compara las dos.
    """
    rango = _rango(filtro)
    poda = _condiciones(
        [("_id", _prefijo(filtro)), ("versiones_anteriores.reemplazada_en", rango or {"$type": "date"})]
    )
    exacto = _condiciones(
        [
            ("pares.a.reemplazada_en", rango),
            ("pares.a.agente_id", filtro.actor),
        ]
    )
    vigente = {"version": "$version"}
    for grupo in campos:
        for campo in grupo:
            vigente[campo] = f"${campo}"
    return [
        *_coincidir(poda),
        {"$set": {"lineas": {"$concatArrays": ["$versiones_anteriores", [vigente]]}}},
        {
            "$set": {
                "pares": {
                    "$map": {
                        "input": {"$range": [0, {"$size": "$versiones_anteriores"}]},
                        "as": "i",
                        "in": {
                            "a": {"$arrayElemAt": ["$lineas", "$$i"]},
                            "b": {"$arrayElemAt": ["$lineas", {"$add": ["$$i", 1]}]},
                        },
                    }
                }
            }
        },
        {"$unwind": "$pares"},
        *_coincidir(exacto),
        {
            "$project": {
                "_id": 0,
                "fecha": "$pares.a.reemplazada_en",
                "actor": {"$ifNull": ["$pares.a.agente_id", None]},
                "accion": {"$literal": accion},
                "objeto": "$_id",
                "detalle": {
                    "$let": {
                        "vars": {"a": "$pares.a", "b": "$pares.b"},
                        "in": {
                            "$concat": [
                                rotulo,
                                " v",
                                {"$toString": "$$a.version"},
                                " -> v",
                                {"$toString": "$$b.version"},
                                ": ",
                                _cambios(campos),
                            ]
                        },
                    }
                },
                "origen": {"$literal": origen},
            }
        },
        *_etapa_accion(filtro),
    ]


def rama_listas_riesgo(filtro: FiltroBitacora) -> list[dict[str, Any]]:
    """Altas (y reactivaciones: `fecha_reporte` se renueva). `objeto` es la cuenta."""
    pre = _condiciones(
        [
            ("fecha_reporte", _rango(filtro) or {"$type": "date"}),
            ("cuenta", _prefijo(filtro)),
            ("agente_id", filtro.actor),
        ]
    )
    return [
        {"$match": pre},
        {
            "$project": {
                "_id": 0,
                "fecha": "$fecha_reporte",
                "actor": {"$ifNull": ["$agente_id", None]},
                "accion": {"$literal": "lista_riesgo_alta"},
                "objeto": "$cuenta",
                "detalle": {"$concat": ["$_id", ": ", "$motivo"]},
                "origen": {"$literal": "listas_riesgo"},
            }
        },
        *_etapa_accion(filtro),
    ]


def rama_casos(filtro: FiltroBitacora) -> list[dict[str, Any]]:
    """Aperturas de caso por escalacion. Los casos de antes de T-56 no traen fecha: fuera."""
    pre = _condiciones(
        [
            ("fecha_apertura", _rango(filtro) or {"$type": "date"}),
            ("_id", _prefijo(filtro)),
            ("agente_id", filtro.actor),
        ]
    )
    unidas = {
        "$reduce": {
            "input": "$alertas",
            "initialValue": "",
            "in": {
                "$concat": ["$$value", {"$cond": [{"$eq": ["$$value", ""]}, "", ", "]}, "$$this"]
            },
        }
    }
    return [
        {"$match": pre},
        {
            "$project": {
                "_id": 0,
                "fecha": "$fecha_apertura",
                "actor": {"$ifNull": ["$agente_id", None]},
                "accion": {"$literal": "caso_abierto"},
                "objeto": "$_id",
                "detalle": {
                    "$concat": [
                        {"$toString": {"$size": "$alertas"}},
                        " alerta(s): ",
                        unidas,
                        {
                            "$cond": [
                                {"$gt": ["$cuenta_destino", None]},
                                {"$concat": [" hacia ", "$cuenta_destino"]},
                                "",
                            ]
                        },
                    ]
                },
                "origen": {"$literal": "casos"},
            }
        },
        *_etapa_accion(filtro),
    ]


def rama_notificaciones(filtro: FiltroBitacora) -> list[dict[str, Any]]:
    """Envios de D2 por canal. Sin actor: lo hace el sistema. `objeto` es la alerta."""
    # `agente_id` no se guarda hoy en notificaciones: con filtro de actor la rama queda vacia.
    pre = _condiciones(
        [
            ("fecha", _rango(filtro)),
            ("alerta_id", _prefijo(filtro)),
            ("agente_id", filtro.actor),
        ]
    )
    error = {"$ifNull": ["$detalle_error", ""]}
    return [
        *_coincidir(pre),
        {
            "$project": {
                "_id": 0,
                "fecha": "$fecha",
                "actor": {"$ifNull": ["$agente_id", None]},
                "accion": {"$concat": ["notificacion_", "$estado_envio"]},
                "objeto": "$alerta_id",
                "detalle": {
                    "$concat": [
                        "$_id",
                        " por ",
                        "$canal",
                        {
                            "$cond": [
                                {"$eq": [error, ""]},
                                "",
                                {"$concat": [": ", error]},
                            ]
                        },
                    ]
                },
                "origen": {"$literal": "notificaciones"},
            }
        },
        *_etapa_accion(filtro),
    ]


def construir_pipeline(filtro: FiltroBitacora, tope: int) -> list[dict[str, Any]]:
    """El pipeline completo, sobre la coleccion `alertas`. `tope` = filas que se piden."""
    if not 1 <= tope <= TOPE_MAXIMO:
        raise ValueError(f"tope fuera de rango: {tope}")
    uniones = [
        ("reglas_deteccion", rama_versiones(
            filtro, origen="reglas_deteccion", accion="regla_editada",
            campos=CAMPOS_REGLA, rotulo="regla",
        )),
        ("politica_deteccion", rama_versiones(
            filtro, origen="politica_deteccion", accion="politica_editada",
            campos=CAMPOS_POLITICA, rotulo="politica",
        )),
        ("listas_riesgo", rama_listas_riesgo(filtro)),
        ("casos", rama_casos(filtro)),
        ("notificaciones", rama_notificaciones(filtro)),
    ]
    return [
        *rama_alertas(filtro),
        *({"$unionWith": {"coll": coleccion, "pipeline": etapas}} for coleccion, etapas in uniones),
        {
            "$facet": {
                "pagina": [{"$sort": ORDEN}, {"$limit": tope}],
                "total": [{"$count": "n"}],
            }
        },
    ]


# Fecha descendente; el resto desempata para que el orden y la paginacion sean estables.
ORDEN = {"fecha": -1, "objeto": -1, "accion": 1, "actor": 1, "detalle": 1}


# -- Fuente en disco: los respaldos --------------------------------------------------------


def eventos_de_manifiesto(manifiesto: Any) -> list[dict[str, Any]]:
    """Un manifiesto de respaldo (`app/respaldos/manifiesto.py`) como eventos de la bitacora."""
    eventos = [
        {
            "fecha": manifiesto.creado_utc,
            "actor": manifiesto.creado_por,
            "accion": "respaldo_creado",
            "objeto": manifiesto.respaldo_id,
            "detalle": (
                f"{manifiesto.total_documentos} documento(s), "
                f"{manifiesto.tamano_bytes} bytes, base {manifiesto.base}"
            ),
            "origen": "respaldos",
        }
    ]
    verificacion = manifiesto.verificacion
    if verificacion is not None:
        eventos.append(
            {
                "fecha": verificacion.verificado_utc,
                "actor": verificacion.verificado_por,
                "accion": "respaldo_verificado",
                "objeto": manifiesto.respaldo_id,
                "detalle": f"resultado {verificacion.resultado}",
                "origen": "respaldos",
            }
        )
    return eventos


def coincide(evento: Mapping[str, Any], filtro: FiltroBitacora) -> bool:
    """El mismo filtro que el pipeline, para las fuentes que se agregan en Python."""
    fecha = evento["fecha"]
    if filtro.desde is not None and fecha < filtro.desde:
        return False
    if filtro.hasta is not None and fecha > filtro.hasta:
        return False
    if filtro.actor is not None and evento["actor"] != filtro.actor:
        return False
    if filtro.acciones and evento["accion"] not in filtro.acciones:
        return False
    return filtro.objeto is None or str(evento["objeto"]).startswith(filtro.objeto)


def fusionar(*listas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Mezcla listas de eventos con el orden de `ORDEN`: fecha desc, objeto desc, accion asc..."""
    todos = [dict(e) for lista in listas for e in lista]
    for evento in todos:  # sin tz_aware pymongo devuelve fechas ingenuas (UTC)
        if evento["fecha"].tzinfo is None:
            evento["fecha"] = evento["fecha"].replace(tzinfo=UTC)
    for campo, sentido in reversed(list(ORDEN.items())):
        todos.sort(key=lambda e, c=campo: (e[c] is not None, e[c] if e[c] is not None else ""),
                   reverse=sentido == -1)
    return todos


@runtime_checkable
class ProtocoloRepoBitacora(Protocol):
    async def consultar(
        self, filtro: FiltroBitacora, tope: int
    ) -> tuple[list[dict[str, Any]], int]: ...


class RepoBitacora:
    def __init__(self, base: AsyncDatabase) -> None:
        self.base = base

    async def consultar(
        self, filtro: FiltroBitacora, tope: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Las `tope` filas mas recientes de las fuentes de Mongo y cuantas cumplen en total."""
        cursor = await self.base["alertas"].aggregate(
            construir_pipeline(filtro, tope), allowDiskUse=True
        )
        resultado = await cursor.to_list(length=1)
        if not resultado:
            return [], 0
        pagina = resultado[0]["pagina"]
        total = resultado[0]["total"][0]["n"] if resultado[0]["total"] else 0
        return pagina, total

    async def explicar(self, filtro: FiltroBitacora, tope: int) -> dict[str, Any]:
        """`explain` con executionStats del pipeline (para medir y documentar el costo)."""
        return await self.base.command(
            {
                "explain": {
                    "aggregate": "alertas",
                    "pipeline": construir_pipeline(filtro, tope),
                    "cursor": {},
                    "allowDiskUse": True,
                },
                "verbosity": "executionStats",
            }
        )
