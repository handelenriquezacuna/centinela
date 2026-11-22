"""Lectura de H-10: transacciones etiquetadas cruzadas con sus alertas.

Es la UNICA consulta del sistema que lee `transacciones.simulacion`. El motor no la lee;
este repositorio existe para medirlo despues de los hechos.

Una agregacion sobre `transacciones`:

1. `$match` de lo que trae etiqueta (lo que escribio el generador);
2. `$lookup` a `alertas` por `transaccion_id`, que tiene el indice unico
   `idx_alertas_transaccion_id`: cada transaccion es una busqueda puntual;
3. `$group` por `(fraude, patron, episodio)` con el total y cuantas tienen alerta.

Lo legitimo cae en una sola fila y el fraude en una por episodio: a Python llegan unas
decenas de filas, no miles de documentos.
"""

from __future__ import annotations

from pymongo.asynchronous.database import AsyncDatabase

from app.deteccion.efectividad import FilaEtiquetada

TRANSACCIONES = "transacciones"
ALERTAS = "alertas"

PIPELINE_ETIQUETADAS: list[dict] = [
    {"$match": {"simulacion": {"$exists": True}}},
    {
        "$lookup": {
            "from": ALERTAS,
            "localField": "_id",
            "foreignField": "transaccion_id",
            "pipeline": [{"$project": {"_id": 1}}],
            "as": "alerta",
        }
    },
    {
        "$group": {
            "_id": {
                "fraude": "$simulacion.fraude",
                "patron": "$simulacion.patron",
                "episodio": "$simulacion.episodio",
            },
            "transacciones": {"$sum": 1},
            "alertadas": {"$sum": {"$cond": [{"$gt": [{"$size": "$alerta"}, 0]}, 1, 0]}},
        }
    },
]


class RepoEfectividad:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def leer_filas_etiquetadas(self) -> tuple[list[FilaEtiquetada], int]:
        """Los grupos etiquetados y el total de alertas de la coleccion.

        El total se lee despues del cruce: si D1 sigue escribiendo, el total puede ser
        mayor, nunca menor (las alertas no se borran), y el calculo lo tolera.
        """
        cursor = await self._base[TRANSACCIONES].aggregate(PIPELINE_ETIQUETADAS)
        grupos = await cursor.to_list(length=None)
        alertas_totales = await self._base[ALERTAS].count_documents({})
        filas = [
            FilaEtiquetada(
                fraude=bool(grupo["_id"].get("fraude")),
                patron=grupo["_id"].get("patron"),
                episodio=grupo["_id"].get("episodio"),
                transacciones=int(grupo["transacciones"]),
                alertadas=int(grupo["alertadas"]),
            )
            for grupo in grupos
        ]
        return filas, alertas_totales
