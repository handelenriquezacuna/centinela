"""H-22 · Vista materializada `indicadores_diarios`.

`indicadores_diarios` es una VISTA MATERIALIZADA: una coleccion derivada de `alertas`
que se calcula con un pipeline de agregacion y se guarda con `$merge`, para que el
tablero (H-20) abra leyendo un documento por dia y no recorriendo todas las alertas.
Nunca se escribe a mano ni desde la API: la unica escritora es la funcion de este
modulo. Si se borra, se reconstruye con `python tareas.py indicadores`.

El pipeline, en un renglon:

    $match(fecha_creacion en [desde, hasta+1 dia)) -> $group(dia CR) -> $sort(_id)
    -> $project(forma del documento) -> $merge(on _id, whenMatched replace)

- El dia se corta en hora de Costa Rica (UTC-6, sin horario de verano desde 1991): el
  `$match` usa los limites de ese dia convertidos a UTC y el `$group` agrupa con
  `$dateToString` y `timezone: "-06:00"`. La fecha resultante es texto YYYY-MM-DD, la
  unica excepcion al contrato de fechas.
- Idempotente: el `_id` es la fecha y `whenMatched: "replace"` pisa el documento entero;
  correr dos veces el mismo rango deja los mismos documentos (solo cambia `calculado_en`).
- Un dia del rango sin alertas no sale del `$group`; se escribe aparte con ceros
  (`$setOnInsert`, solo si no existe) para que el tablero lea un cero y no un hueco.
- El documento es exactamente lo que acepta el validador de
  `scripts/semilla/01-colecciones.js` (`additionalProperties: false`): fecha, total,
  conteo por severidad, por estado, por agente (`sin_asignar` si no tiene), por hora de
  CR (las 24 claves "00" a "23"), `calculado_en` y `esquema_version`.
  Los enteros salen int32 porque `$sum` de enteros pequenos lo es.

KPIs del supervisor (T-51), todos opcionales en el validador y calculados sobre las
alertas CREADAS ese dia (dia de CR), con el estado que tienen al correr el pipeline:

- `decision_cantidad` y `decision_segundos_total`: de las alertas con una transicion a un
  estado terminal (confirmada, descartada o escalada), cuantas son y la suma de segundos
  entre `fecha_creacion` y la PRIMERA de esas transiciones segun `historial` (no existe
  un campo fecha_resolucion). Se guarda suma y cuenta, y no el promedio, para que el
  tablero pueda promediar varios dias ponderando y no promediando promedios.
- `monto_protegido_crc`: suma de `monto_crc` de las alertas confirmadas o escaladas.
- `pendientes`: alertas en `nueva` o `en_revision`; `pendiente_mas_antigua`: la
  `fecha_creacion` de la mas vieja (ausente si no hay pendientes).
La tasa de confirmacion no se guarda: sale de `por_estado` (confirmadas sobre confirmadas
mas descartadas), un solo origen para la cifra.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.modelos import COLECCION_ALERTAS

COLECCION_INDICADORES = "indicadores_diarios"
ESQUEMA_VERSION_INDICADORES = 1

# Costa Rica: UTC-6 fijo. Se usa el desfase y no un nombre de zona para no depender de
# la base de datos de zonas horarias del sistema (Windows no la trae).
ZONA_CR = timezone(timedelta(hours=-6), "America/Costa_Rica")
ZONA_CR_MONGO = "-06:00"

SEVERIDADES = ("MEDIA", "ALTA", "CRITICA")
HORAS = tuple(f"{h:02d}" for h in range(24))
SIN_ASIGNAR = "sin_asignar"
ESTADOS = ("nueva", "en_revision", "confirmada", "descartada", "escalada")
ESTADOS_TERMINALES = ("confirmada", "descartada", "escalada")
ESTADOS_PROTEGEN = ("confirmada", "escalada")
ESTADOS_PENDIENTES = ("nueva", "en_revision")


def hoy_costa_rica(ahora: datetime | None = None) -> date:
    """El dia calendario de Costa Rica en este momento."""
    return (ahora or datetime.now(UTC)).astimezone(ZONA_CR).date()


def limites_utc(desde: date, hasta: date) -> tuple[datetime, datetime]:
    """`[inicio, fin)` en UTC de los dias `desde` y `hasta` (ambos incluidos) en CR."""
    if hasta < desde:
        raise ValueError("hasta no puede ser anterior a desde")
    inicio = datetime(desde.year, desde.month, desde.day, tzinfo=ZONA_CR)
    fin = datetime(hasta.year, hasta.month, hasta.day, tzinfo=ZONA_CR) + timedelta(days=1)
    return inicio.astimezone(UTC), fin.astimezone(UTC)


def dias_del_rango(desde: date, hasta: date) -> list[str]:
    """Las fechas YYYY-MM-DD del rango, ambos extremos incluidos."""
    return [(desde + timedelta(days=n)).isoformat() for n in range((hasta - desde).days + 1)]


def _contar(campo: str, valor: str) -> dict[str, Any]:
    return {"$sum": {"$cond": [{"$eq": [f"${campo}", valor]}, 1, 0]}}


def _segundos_hasta_decision() -> dict[str, Any]:
    """Segundos de `fecha_creacion` a la primera transicion terminal del `historial`.

    `null` si la alerta no tiene ninguna (sigue abierta); `$sum` ignora los nulos.
    Nunca negativo: un reloj atrasado no resta tiempo al promedio.
    """
    fechas = {
        "$map": {
            "input": {
                "$filter": {
                    "input": "$historial",
                    "cond": {"$in": ["$$this.accion", list(ESTADOS_TERMINALES)]},
                }
            },
            "in": "$$this.fecha",
        }
    }
    return {
        "$let": {
            "vars": {"primera": {"$min": fechas}},
            "in": {
                "$cond": [
                    {"$eq": ["$$primera", None]},
                    None,
                    {
                        "$max": [
                            0,
                            {
                                "$dateDiff": {
                                    "startDate": "$fecha_creacion",
                                    "endDate": "$$primera",
                                    "unit": "second",
                                }
                            },
                        ]
                    },
                ]
            },
        }
    }


def construir_pipeline(desde: date, hasta: date) -> list[dict[str, Any]]:
    """El pipeline de H-22 para el rango. Funcion pura: se prueba sin base de datos."""
    inicio, fin = limites_utc(desde, hasta)
    grupo: dict[str, Any] = {
        "_id": {
            "$dateToString": {
                "format": "%Y-%m-%d",
                "date": "$fecha_creacion",
                "timezone": ZONA_CR_MONGO,
            }
        },
        "total": {"$sum": 1},
        "agentes": {"$push": {"$ifNull": ["$agente_id", SIN_ASIGNAR]}},
        "horas": {
            "$push": {
                "$dateToString": {
                    "format": "%H",
                    "date": "$fecha_creacion",
                    "timezone": ZONA_CR_MONGO,
                }
            }
        },
    }
    grupo["decision_cantidad"] = {"$sum": {"$cond": [{"$ne": ["$_decision_s", None]}, 1, 0]}}
    grupo["decision_segundos_total"] = {"$sum": "$_decision_s"}
    grupo["monto_protegido"] = {
        "$sum": {"$cond": [{"$in": ["$estado", list(ESTADOS_PROTEGEN)]}, "$monto_crc", 0]}
    }
    pendiente = {"$in": ["$estado", list(ESTADOS_PENDIENTES)]}
    grupo["pendientes"] = {"$sum": {"$cond": [pendiente, 1, 0]}}
    grupo["pendiente_antigua"] = {
        "$min": {"$cond": [pendiente, "$fecha_creacion", None]}
    }
    for severidad in SEVERIDADES:
        grupo[f"sev_{severidad}"] = _contar("severidad", severidad)
    for estado in ESTADOS:
        grupo[f"est_{estado}"] = _contar("estado", estado)

    return [
        {"$match": {"fecha_creacion": {"$gte": inicio, "$lt": fin}}},
        {"$addFields": {"_decision_s": _segundos_hasta_decision()}},
        {"$group": grupo},
        {"$sort": {"_id": 1}},
        {
            "$project": {
                "_id": 1,
                "fecha": "$_id",
                "total_alertas": "$total",
                "por_severidad": {s: f"$sev_{s}" for s in SEVERIDADES},
                "por_estado": {e: f"$est_{e}" for e in ESTADOS},
                "por_agente": {
                    "$arrayToObject": {
                        "$map": {
                            "input": {"$setUnion": ["$agentes", []]},
                            "as": "a",
                            "in": {
                                "k": "$$a",
                                "v": {
                                    "$size": {
                                        "$filter": {
                                            "input": "$agentes",
                                            "cond": {"$eq": ["$$this", "$$a"]},
                                        }
                                    }
                                },
                            },
                        }
                    }
                },
                "por_hora": {
                    "$arrayToObject": {
                        "$map": {
                            "input": list(HORAS),
                            "as": "h",
                            "in": {
                                "k": "$$h",
                                "v": {
                                    "$size": {
                                        "$filter": {
                                            "input": "$horas",
                                            "cond": {"$eq": ["$$this", "$$h"]},
                                        }
                                    }
                                },
                            },
                        }
                    }
                },
                "decision_cantidad": "$decision_cantidad",
                "decision_segundos_total": "$decision_segundos_total",
                "monto_protegido_crc": "$monto_protegido",
                "pendientes": "$pendientes",
                "pendiente_mas_antigua": {
                    "$cond": [{"$eq": ["$pendiente_antigua", None]}, "$$REMOVE", "$pendiente_antigua"]
                },
                "calculado_en": "$$NOW",
                "esquema_version": {"$literal": ESQUEMA_VERSION_INDICADORES},
            }
        },
        {
            "$merge": {
                "into": COLECCION_INDICADORES,
                "on": "_id",
                "whenMatched": "replace",
                "whenNotMatched": "insert",
            }
        },
    ]


def documento_en_ceros(fecha: str, calculado_en: datetime) -> dict[str, Any]:
    """Un dia sin alertas, con la misma forma que el pipeline."""
    return {
        "_id": fecha,
        "fecha": fecha,
        "total_alertas": 0,
        "por_severidad": dict.fromkeys(SEVERIDADES, 0),
        "por_estado": dict.fromkeys(ESTADOS, 0),
        "por_agente": {},
        "por_hora": dict.fromkeys(HORAS, 0),
        "decision_cantidad": 0,
        "decision_segundos_total": 0,
        "monto_protegido_crc": 0,
        "pendientes": 0,
        "calculado_en": calculado_en,
        "esquema_version": ESQUEMA_VERSION_INDICADORES,
    }


class RepoIndicadores:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def recalcular(self, desde: date, hasta: date) -> int:
        """Recalcula los dias del rango y devuelve cuantos documentos hay en el rango."""
        cursor = await self._base[COLECCION_ALERTAS].aggregate(construir_pipeline(desde, hasta))
        await cursor.to_list(length=None)  # `$merge` no devuelve filas; esto lo ejecuta
        coleccion = self._base[COLECCION_INDICADORES]
        dias = dias_del_rango(desde, hasta)
        existentes = {
            doc["_id"]
            async for doc in coleccion.find({"_id": {"$in": dias}}, {"_id": 1})
        }
        ahora = datetime.now(UTC)
        for fecha in dias:
            if fecha not in existentes:
                await coleccion.update_one(
                    {"_id": fecha},
                    {"$setOnInsert": documento_en_ceros(fecha, ahora)},
                    upsert=True,
                )
        return len(dias)

    async def leer_rango(self, desde: date, hasta: date) -> list[dict[str, Any]]:
        """Los documentos materializados del rango, en orden de fecha."""
        filtro = {"_id": {"$gte": desde.isoformat(), "$lte": hasta.isoformat()}}
        return await self._base[COLECCION_INDICADORES].find(filtro).sort("_id", 1).to_list(
            length=None
        )
