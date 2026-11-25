"""T-63 · Rasgos de grafo por cuenta (vista materializada para R-11).

Cada cuenta destino con movimiento reciente tiene un documento en `deteccion_estado`
(clase `rasgo_grafo`, `_id` `d3_rasgos_grafo:<cuenta>`) con lo que R-11 necesita: cuanto
recibio, cuanto reenvio en `ventana_minutos`, a cuantos destinos, de cuantos origenes y
cuantos saltos sigue el dinero. La definicion de cada numero esta en
`app/deteccion/rasgos_grafo.py`; este modulo es el mismo calculo hecho por Mongo.

Donde vive. El contrato fija 13 colecciones y los rasgos son estado DERIVADO del
detector, igual que el token de D1 y el descarte: por eso comparten `deteccion_estado`,
que ya distingue sus documentos por `clase` (el validador del bloque 13 de
`01-colecciones.js` tiene una rama por clase). `perfiles_comportamiento` no sirve: su
`_id` es el cliente, y una mula destino es una cuenta SINPE que muchas veces no es cliente
ni tiene documento en `cuentas`.

El pipeline, sobre `transacciones`:

    $match(fecha en [desde, corte))                 indice idx_transacciones_fecha
    -> $group(cuenta_destino)                       fan_in, recibos, monto recibido
    -> $graphLookup(sinpe_origen -> cuenta_destino) salidas (nivel 0) y la cadena
    -> $addFields(salidas dentro de la ventana, reenviado, minutos, saltos, fan_out)
    -> $project(forma del documento) -> $merge(on _id, replace)

Es el calculo por lotes que un motor de grafos hace al materializar propiedades de nodo
(grado de entrada y de salida, flujo reenviado, profundidad de la cadena), hecho con el
recorrido `$graphLookup` y una escritura idempotente. Ver docs/06-plataforma.md.

Idempotente: el `_id` es la cuenta y `$merge` reemplaza el documento; correrlo dos veces
con el mismo corte deja los mismos documentos. Despues de cada corrida se escribe el
documento de corrida (`_id` `d3_rasgos_grafo`, clase `rasgo_grafo_corrida`): dice de que
rango son los rasgos y hasta cuando valen. Una cuenta SIN documento por cuenta no tuvo
movimientos en ese rango, y su rasgo es el de ceros; sin documento de corrida, en cambio,
no hay rasgos y R-11 queda como no evaluada. Los documentos de corridas anteriores se
borran al final.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.deteccion.evaluador import RasgoRed
from app.deteccion.rasgos_grafo import (
    CLASE_CORRIDA,
    CLASE_RASGO,
    NOMBRE_RASGOS,
    SALTOS_MAXIMOS,
    TIPO_REGLA_RED,
    id_rasgo,
)
from app.modelos import (
    COLECCION_DETECCION_ESTADO,
    COLECCION_TRANSACCIONES,
    ESQUEMA_VERSION,
)
from app.nucleo.config import AjustesRasgosGrafo

COLECCION_REGLAS = "reglas_deteccion"
ESQUEMA_VERSION_RASGOS = ESQUEMA_VERSION


def _ms(minutos: int) -> int:
    return minutos * 60_000


def construir_pipeline(
    desde: datetime, corte: datetime, ventana_minutos: int, vigencia_segundos: int
) -> list[dict[str, Any]]:
    """El pipeline de los rasgos. Funcion pura: se prueba sin base de datos."""
    rango = {"$gte": desde, "$lt": corte}
    # Un recibo r habilita una salida s si r.f <= s.fecha <= r.f + ventana.
    habilita = {
        "$anyElementTrue": {
            "$map": {
                "input": "$recibos",
                "as": "r",
                "in": {
                    "$and": [
                        {"$lte": ["$$r.f", "$$s.fecha"]},
                        {
                            "$lte": [
                                "$$s.fecha",
                                {
                                    "$dateAdd": {
                                        "startDate": "$$r.f",
                                        "unit": "minute",
                                        "amount": ventana_minutos,
                                    }
                                },
                            ]
                        },
                    ]
                },
            }
        }
    }
    entrada_anterior = {
        "$max": {
            "$map": {
                "input": {
                    "$filter": {
                        "input": "$recibos",
                        "as": "r",
                        "cond": {"$lte": ["$$r.f", "$$this.fecha"]},
                    }
                },
                "as": "r",
                "in": "$$r.f",
            }
        }
    }
    return [
        {"$match": {"fecha": rango}},
        {
            "$group": {
                "_id": "$cuenta_destino",
                "origenes": {"$addToSet": "$cuenta_origen"},
                "recibos": {"$push": {"f": "$fecha"}},
                "recibido": {"$sum": "$monto_crc"},
            }
        },
        {
            "$graphLookup": {
                "from": COLECCION_TRANSACCIONES,
                "startWith": "$_id",
                "connectFromField": "cuenta_destino",
                "connectToField": "sinpe_origen",
                "as": "cadena",
                "maxDepth": SALTOS_MAXIMOS - 1,
                "depthField": "nivel",
                "restrictSearchWithMatch": {"fecha": rango},
            }
        },
        {
            "$addFields": {
                "salidas": {
                    "$filter": {
                        "input": "$cadena",
                        "as": "c",
                        "cond": {"$eq": ["$$c.nivel", 0]},
                    }
                }
            }
        },
        {
            "$addFields": {
                "contadas": {
                    "$filter": {"input": "$salidas", "as": "s", "cond": habilita}
                }
            }
        },
        {
            "$addFields": {
                "reenviado": {"$min": [{"$sum": "$contadas.monto_crc"}, "$recibido"]},
                "minutos": {
                    "$ifNull": [
                        {
                            "$max": {
                                "$map": {
                                    "input": "$contadas",
                                    "in": {
                                        "$toInt": {
                                            "$ceil": {
                                                "$divide": [
                                                    {
                                                        "$subtract": [
                                                            "$$this.fecha",
                                                            entrada_anterior,
                                                        ]
                                                    },
                                                    60_000,
                                                ]
                                            }
                                        }
                                    },
                                }
                            }
                        },
                        0,
                    ]
                },
            }
        },
        {
            "$project": {
                "_id": {"$concat": [f"{NOMBRE_RASGOS}:", "$_id"]},
                "clase": {"$literal": CLASE_RASGO},
                "disparador": {"$literal": NOMBRE_RASGOS},
                "cuenta": "$_id",
                "ventana_minutos": {"$literal": ventana_minutos},
                "desde": {"$literal": desde},
                "corte": {"$literal": corte},
                "vigencia_segundos": {"$literal": vigencia_segundos},
                "fan_in": {"$size": "$origenes"},
                "fan_out": {"$size": {"$setUnion": ["$salidas.cuenta_destino", []]}},
                "monto_recibido_crc": {"$toLong": "$recibido"},
                "monto_reenviado_crc": {"$toLong": "$reenviado"},
                "proporcion_pb": {
                    "$toInt": {
                        "$floor": {
                            "$divide": [{"$multiply": ["$reenviado", 10_000]}, "$recibido"]
                        }
                    }
                },
                "minutos_reenvio": "$minutos",
                "saltos": {
                    "$cond": [
                        {"$eq": [{"$size": "$cadena"}, 0]},
                        0,
                        {"$add": [{"$toInt": {"$max": "$cadena.nivel"}}, 1]},
                    ]
                },
                "calculado_en": "$$NOW",
                "esquema_version": {"$literal": ESQUEMA_VERSION_RASGOS},
            }
        },
        {
            "$merge": {
                "into": COLECCION_DETECCION_ESTADO,
                "on": "_id",
                "whenMatched": "replace",
                "whenNotMatched": "insert",
            }
        },
    ]


def a_rasgo_red(
    documento: dict[str, Any] | None, corrida: dict[str, Any], cuenta: str
) -> RasgoRed:
    """Rasgo de una cuenta; en ceros, con el rango de la corrida, si no tiene documento."""
    base = {
        "cuenta": cuenta,
        "ventana_minutos": corrida["ventana_minutos"],
        "desde": corrida["desde"],
        "corte": corrida["corte"],
        "vigencia_segundos": corrida["vigencia_segundos"],
    }
    if documento is None:
        return RasgoRed(
            **base,
            fan_in=0,
            fan_out=0,
            monto_recibido_crc=0,
            monto_reenviado_crc=0,
            proporcion_pb=0,
            minutos_reenvio=0,
            saltos=0,
        )
    return RasgoRed(
        **{**base, "ventana_minutos": documento["ventana_minutos"]},
        fan_in=documento["fan_in"],
        fan_out=documento["fan_out"],
        monto_recibido_crc=int(documento["monto_recibido_crc"]),
        monto_reenviado_crc=int(documento["monto_reenviado_crc"]),
        proporcion_pb=documento["proporcion_pb"],
        minutos_reenvio=documento["minutos_reenvio"],
        saltos=documento["saltos"],
    )


def _con_zona(fecha: datetime) -> datetime:
    return fecha if fecha.tzinfo is not None else fecha.replace(tzinfo=UTC)


class RepoRasgosGrafo:
    """Escribe y lee los rasgos de grafo. Los recalcula D3 o `python -m`; los lee D1."""

    def __init__(self, base: AsyncDatabase, ajustes: AjustesRasgosGrafo | None = None) -> None:
        self._base = base
        self._ajustes = ajustes or AjustesRasgosGrafo()
        self._estado = base[COLECCION_DETECCION_ESTADO]

    async def ventana_de_la_regla(self, *, solo_activa: bool = False) -> int | None:
        """`ventana_minutos` de la regla `red_mulas`, o None si no hay o no la declara."""
        filtro: dict[str, Any] = {"tipo": TIPO_REGLA_RED}
        if solo_activa:
            filtro["activa"] = True
        regla = await self._base[COLECCION_REGLAS].find_one(filtro, {"parametros": 1})
        if regla is None:
            return None
        ventana = regla.get("parametros", {}).get("ventana_minutos")
        return max(1, int(ventana)) if ventana is not None else None

    async def recalcular_si_activa(self, corte: datetime) -> int:
        """Lo que hace D3 en cada vuelta: nada si R-11 esta apagada, y si no, recalcula.

        Apagada, no se gasta ni una lectura en materializar algo que nadie consulta. Al
        encenderla, el primer recalculo ocurre en la vuelta siguiente de D3 y mientras
        tanto R-11 informa que no hay rasgos.
        """
        ventana = await self.ventana_de_la_regla(solo_activa=True)
        if ventana is None:
            return 0
        return await self.recalcular(
            corte,
            ventana_minutos=ventana,
            horizonte=timedelta(hours=self._ajustes.horizonte_horas),
        )

    async def recalcular(
        self, corte: datetime, *, ventana_minutos: int, horizonte: timedelta
    ) -> int:
        """Recalcula los rasgos del horizonte y devuelve cuantas cuentas quedaron.

        Orden: `$merge` de las cuentas, documento de corrida y, al final, borrado de lo
        que sobra de corridas anteriores. Las fechas van truncadas a milisegundo, la
        precision de Mongo, para que la frontera `[desde, corte)` sea la misma al leer.
        """
        corte = _a_milisegundos(_con_zona(corte))
        desde = corte - horizonte
        pipeline = construir_pipeline(
            desde, corte, ventana_minutos, self._ajustes.vigencia_segundos
        )
        # `maxTimeMS`: un recalculo que tarda mas de lo que vale el rasgo ya nace viejo, y
        # sin indices (una base mal sembrada) seria una busqueda por cuenta sobre toda la
        # coleccion. Mongo lo corta del lado del servidor; D3 lo anota y reintenta.
        cursor = await self._base[COLECCION_TRANSACCIONES].aggregate(
            pipeline,
            allowDiskUse=True,
            maxTimeMS=self._ajustes.vigencia_segundos * 1000,
        )
        await cursor.to_list(length=None)  # `$merge` no devuelve filas; esto lo ejecuta
        cuentas = await self._estado.count_documents(
            {"clase": CLASE_RASGO, "corte": corte}
        )
        await self._estado.replace_one(
            {"_id": NOMBRE_RASGOS},
            {
                "_id": NOMBRE_RASGOS,
                "clase": CLASE_CORRIDA,
                "disparador": NOMBRE_RASGOS,
                "ventana_minutos": ventana_minutos,
                "desde": desde,
                "corte": corte,
                "vigencia_segundos": self._ajustes.vigencia_segundos,
                "cuentas": cuentas,
                "actualizado_en": datetime.now(UTC),
                "esquema_version": ESQUEMA_VERSION_RASGOS,
            },
            upsert=True,
        )
        await self._estado.delete_many({"clase": CLASE_RASGO, "corte": {"$lt": corte}})
        return cuentas

    async def leer_rasgo(self, cuenta: str) -> RasgoRed | None:
        """El rasgo de `cuenta` para D1, o None si nunca hubo una corrida.

        Una sola lectura por `_id`: el documento de la cuenta y el de la corrida.
        """
        documentos = {
            doc["_id"]: doc
            async for doc in self._estado.find(
                {"_id": {"$in": [NOMBRE_RASGOS, id_rasgo(cuenta)]}}
            )
        }
        corrida = documentos.get(NOMBRE_RASGOS)
        if corrida is None:
            return None
        corrida = {
            **corrida,
            "desde": _con_zona(corrida["desde"]),
            "corte": _con_zona(corrida["corte"]),
        }
        return a_rasgo_red(documentos.get(id_rasgo(cuenta)), corrida, cuenta)

    async def explicar(
        self, corte: datetime, *, ventana_minutos: int, horizonte: timedelta
    ) -> dict[str, Any]:
        """`explain("executionStats")` del pipeline, sin el `$merge` final (un `explain`
        no debe escribir). Sirve a la prueba y a docs/06-plataforma.md."""
        corte = _a_milisegundos(_con_zona(corte))
        pipeline = construir_pipeline(
            corte - horizonte, corte, ventana_minutos, self._ajustes.vigencia_segundos
        )[:-1]
        return await self._base.command(
            {
                "explain": {
                    "aggregate": COLECCION_TRANSACCIONES,
                    "pipeline": pipeline,
                    "cursor": {},
                },
                "verbosity": "executionStats",
            }
        )

    async def usos_de_indices(self) -> dict[str, int]:
        """Contador `accesses.ops` de cada indice de `transacciones` (`$indexStats`)."""
        filas = await (
            await self._base[COLECCION_TRANSACCIONES].aggregate([{"$indexStats": {}}])
        ).to_list(length=None)
        return {fila["name"]: int(fila["accesses"]["ops"]) for fila in filas}

    async def mayores_proporciones(self, cantidad: int) -> list[dict[str, Any]]:
        """Las cuentas que mas reenvian, para mostrar en consola."""
        return await (
            self._estado.find({"clase": CLASE_RASGO})
            .sort([("proporcion_pb", DESCENDING), ("monto_recibido_crc", DESCENDING)])
            .limit(cantidad)
            .to_list(length=cantidad)
        )


def _a_milisegundos(fecha: datetime) -> datetime:
    return fecha.replace(microsecond=fecha.microsecond // 1000 * 1000)
