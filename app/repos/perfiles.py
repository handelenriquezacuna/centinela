"""H-21 / T-76 · Vista materializada `perfiles_comportamiento`.

El perfil de cada cliente se calcula con un pipeline sobre `transacciones` y se escribe
con `$merge`, para que el motor lo lea con una busqueda por `_id` (R-01 y R-05) en vez de
recorrer el historial en cada transaccion. La definicion de cada numero, el criterio de
las transacciones que cuentan y el de horario habitual estan en
`app/deteccion/perfiles.py`; este modulo es el mismo calculo hecho por Mongo.

El pipeline:

    $match(fecha en [desde, corte), y cuenta_origen de un cliente si se pide uno)
    -> $lookup(cuentas)                 cliente de la cuenta de origen
    -> $lookup(alertas por transaccion_id, estado distinto de descartada)
                                        las transacciones con alerta viva no cuentan
    -> $group(cliente)                  n, suma, suma de cuadrados, maximo, horas, destinos
    -> $addFields(...)                  histograma por hora, ventana mas corta con el 90 %
                                        de las transferencias, destinos frecuentes
    -> $project(forma del documento) -> $merge(on _id, replace)

La forma del documento es exactamente lo que acepta el validador de
`scripts/semilla/01-colecciones.js` (`additionalProperties: false`): el validador no declara
`calculado_en`, asi que el perfil no lo lleva. El dinero sale `long` y los conteos `int`.
El script de mongosh `scripts/procedimientos/03-recalcular-perfiles.js` tiene este mismo
pipeline; una prueba de integracion compara los documentos de los dos.

Idempotente: el `_id` es el cliente y `$merge` reemplaza el documento. Un cliente sin
transacciones que cuenten en el rango no se toca.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.deteccion.perfiles import (
    COBERTURA_HORARIO_PCT,
    DIAS_POR_OMISION,
    MAX_DESTINOS_FRECUENTES,
    MIN_VECES_FRECUENTE,
)
from app.modelos import COLECCION_ALERTAS, COLECCION_TRANSACCIONES, ESQUEMA_VERSION

COLECCION_PERFILES = "perfiles_comportamiento"
COLECCION_CUENTAS = "cuentas"
ESQUEMA_VERSION_PERFILES = ESQUEMA_VERSION
ZONA_CR_MONGO = "-06:00"
HORAS = [f"{h:02d}" for h in range(24)]


def _a_milisegundos(fecha: datetime) -> datetime:
    return fecha.replace(microsecond=fecha.microsecond // 1000 * 1000)


def construir_pipeline(
    desde: datetime, corte: datetime, cuentas_origen: list[str] | None = None
) -> list[dict[str, Any]]:
    """El pipeline de los perfiles. Funcion pura: se prueba sin base de datos.

    `cuentas_origen` restringe el rango a las cuentas de un cliente (el `$match` usa el
    indice `idx_transacciones_cuenta_origen_fecha`); sin ella, calcula todos los clientes.
    """
    filtro: dict[str, Any] = {"fecha": {"$gte": desde, "$lt": corte}}
    if cuentas_origen is not None:
        filtro["cuenta_origen"] = {"$in": cuentas_origen}

    n = "$n"
    # Redondeo de la mitad hacia arriba con enteros: (2 * suma + n) // (2 * n).
    numerador = {"$add": [{"$multiply": [2, "$suma"]}, n]}
    divisor = {"$multiply": [2, n]}
    promedio = {
        "$toLong": {
            "$divide": [{"$subtract": [numerador, {"$mod": [numerador, divisor]}]}, divisor]
        }
    }
    varianza_por_n2 = {
        "$subtract": [{"$multiply": [n, "$suma_cuadrados"]}, {"$multiply": ["$suma", "$suma"]}]
    }
    desviacion = {
        "$toLong": {"$floor": {"$add": [{"$divide": [{"$sqrt": varianza_por_n2}, n]}, 0.5]}}
    }

    # Para cada inicio `s` (0 a 23), el menor largo `l` (1 a 24) cuya ventana circular reune
    # al menos el 90 % de las transferencias; la clave `l * 100 + s` ordena por largo y, a
    # igual largo, por inicio. Las sumas de ventana salen de las sumas acumuladas `pref`.
    cubre = {
        "$gte": [
            {
                "$multiply": [
                    100,
                    {
                        "$subtract": [
                            {"$arrayElemAt": ["$pref", {"$add": ["$$s", "$$l"]}]},
                            {"$arrayElemAt": ["$pref", "$$s"]},
                        ]
                    },
                ]
            },
            {"$multiply": [COBERTURA_HORARIO_PCT, n]},
        ]
    }
    clave_ventana = {
        "$min": {
            "$map": {
                "input": {"$range": [0, 24]},
                "as": "s",
                "in": {
                    "$add": [
                        {
                            "$multiply": [
                                100,
                                {
                                    "$min": {
                                        "$filter": {
                                            "input": {"$range": [1, 25]},
                                            "as": "l",
                                            "cond": cubre,
                                        }
                                    }
                                },
                            ]
                        },
                        "$$s",
                    ]
                },
            }
        }
    }
    hora_texto = {"$arrayElemAt": [HORAS, {"$toInt": "$_hora_fin"}]}

    return [
        {"$match": filtro},
        {
            "$lookup": {
                "from": COLECCION_CUENTAS,
                "localField": "cuenta_origen",
                "foreignField": "_id",
                "pipeline": [{"$project": {"cliente_id": 1}}],
                "as": "cuenta",
            }
        },
        {"$match": {"cuenta": {"$ne": []}}},
        {
            "$lookup": {
                "from": COLECCION_ALERTAS,
                "localField": "_id",
                "foreignField": "transaccion_id",
                "pipeline": [{"$match": {"estado": {"$ne": "descartada"}}}, {"$project": {"_id": 1}}],
                "as": "alerta_viva",
            }
        },
        {"$match": {"alerta_viva": {"$size": 0}}},
        {
            "$group": {
                "_id": {"$arrayElemAt": ["$cuenta.cliente_id", 0]},
                "n": {"$sum": 1},
                "suma": {"$sum": "$monto_crc"},
                "suma_cuadrados": {"$sum": {"$multiply": ["$monto_crc", "$monto_crc"]}},
                "maximo": {"$max": "$monto_crc"},
                "horas": {
                    "$push": {"$hour": {"date": "$fecha", "timezone": ZONA_CR_MONGO}}
                },
                "destinos": {"$push": "$cuenta_destino"},
            }
        },
        {
            "$addFields": {
                "hist": {
                    "$map": {
                        "input": list(range(24)),
                        "as": "h",
                        "in": {
                            "$size": {
                                "$filter": {
                                    "input": "$horas",
                                    "as": "x",
                                    "cond": {"$eq": ["$$x", "$$h"]},
                                }
                            }
                        },
                    }
                },
                "conteo_destinos": {
                    "$map": {
                        "input": {"$setUnion": ["$destinos", []]},
                        "as": "d",
                        "in": {
                            "cuenta": "$$d",
                            "veces": {
                                "$size": {
                                    "$filter": {
                                        "input": "$destinos",
                                        "as": "x",
                                        "cond": {"$eq": ["$$x", "$$d"]},
                                    }
                                }
                            },
                        },
                    }
                },
            }
        },
        {
            "$addFields": {
                "pref": {
                    "$reduce": {
                        "input": {"$concatArrays": ["$hist", "$hist"]},
                        "initialValue": [0],
                        "in": {
                            "$concatArrays": [
                                "$$value",
                                [{"$add": [{"$arrayElemAt": ["$$value", -1]}, "$$this"]}],
                            ]
                        },
                    }
                },
                "ordenados": {
                    "$sortArray": {"input": "$conteo_destinos", "sortBy": {"veces": -1, "cuenta": 1}}
                },
            }
        },
        {"$addFields": {"clave_ventana": clave_ventana}},
        {
            "$addFields": {
                "_hora_inicio": {"$mod": ["$clave_ventana", 100]},
                "_largo": {
                    "$divide": [
                        {"$subtract": ["$clave_ventana", {"$mod": ["$clave_ventana", 100]}]},
                        100,
                    ]
                },
                "frecuentes": {
                    "$slice": [
                        {
                            "$filter": {
                                "input": "$ordenados",
                                "as": "c",
                                "cond": {"$gte": ["$$c.veces", MIN_VECES_FRECUENTE]},
                            }
                        },
                        MAX_DESTINOS_FRECUENTES,
                    ]
                },
            }
        },
        {
            "$addFields": {
                "_hora_fin": {
                    "$mod": [{"$subtract": [{"$add": ["$_hora_inicio", "$_largo"]}, 1]}, 24]
                },
                "frecuentes": {
                    "$cond": [
                        {"$eq": [{"$size": "$frecuentes"}, 0]},
                        [{"$arrayElemAt": ["$ordenados", 0]}],
                        "$frecuentes",
                    ]
                },
            }
        },
        {
            "$project": {
                "_id": 1,
                "promedio_crc": promedio,
                "maximo_crc": "$maximo",
                "desviacion_crc": desviacion,
                "num_transferencias": "$n",
                "horario_habitual": {
                    "inicio": {
                        "$concat": [
                            {"$arrayElemAt": [HORAS, {"$toInt": "$_hora_inicio"}]},
                            ":00",
                        ]
                    },
                    "fin": {"$concat": [hora_texto, ":59"]},
                },
                "destinos_frecuentes": {
                    "$map": {"input": "$frecuentes", "as": "c", "in": "$$c.cuenta"}
                },
                "esquema_version": {"$literal": ESQUEMA_VERSION_PERFILES},
            }
        },
        {
            "$merge": {
                "into": COLECCION_PERFILES,
                "on": "_id",
                "whenMatched": "replace",
                "whenNotMatched": "insert",
            }
        },
    ]


class RepoPerfiles:
    """Escribe los perfiles con `$merge`. Lo corre D3 (si se activa) o `python tareas.py perfiles`."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def recalcular(
        self, corte: datetime, *, dias: int = DIAS_POR_OMISION, cliente_id: str | None = None
    ) -> int:
        """Recalcula los perfiles del rango `[corte - dias, corte)` y devuelve cuantos hay.

        Con `cliente_id` recalcula solo a ese cliente (una sola busqueda por sus cuentas);
        sin el, a todos. El corte se trunca a milisegundo, la precision de Mongo.
        """
        if dias < 1:
            raise ValueError("dias tiene que ser al menos 1")
        if corte.tzinfo is None:
            corte = corte.replace(tzinfo=UTC)
        corte = _a_milisegundos(corte)
        desde = corte - timedelta(days=dias)

        cuentas: list[str] | None = None
        if cliente_id is not None:
            cuentas = [
                doc["_id"]
                async for doc in self._base[COLECCION_CUENTAS].find(
                    {"cliente_id": cliente_id}, {"_id": 1}
                )
            ]
            if not cuentas:
                return 0

        cursor = await self._base[COLECCION_TRANSACCIONES].aggregate(
            construir_pipeline(desde, corte, cuentas), allowDiskUse=True
        )
        await cursor.to_list(length=None)  # `$merge` no devuelve filas; esto lo ejecuta
        # `$merge` no informa cuantos escribio: se devuelve cuantos perfiles hay ahora.
        return await self._base[COLECCION_PERFILES].count_documents(self._alcance(cliente_id))

    @staticmethod
    def _alcance(cliente_id: str | None) -> dict[str, Any]:
        return {} if cliente_id is None else {"_id": cliente_id}

    async def leer_perfiles(
        self, cliente_id: str | None = None, *, limite: int | None = None
    ) -> list[dict[str, Any]]:
        """Los perfiles materializados, en orden de cliente."""
        cursor = self._base[COLECCION_PERFILES].find(self._alcance(cliente_id)).sort("_id", 1)
        if limite is not None:
            cursor = cursor.limit(limite)
        return await cursor.to_list(length=limite)
