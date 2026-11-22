"""H-23 · Red de cuentas mula: seguir el dinero desde una cuenta.

Devuelve la red como nodos y aristas (JSON listo para dibujar un grafo) y la lista de
cuentas que se PROPONEN para `listas_riesgo`. No escribe nada: proponer es trabajo del
sistema, decidir es del agente.

**Por que no es un `$graphLookup` unico.** `$graphLookup` recorre UNA coleccion uniendo
un campo con otro del mismo espacio de valores. En `transacciones` los dos extremos de
un eslabon estan en espacios distintos: `cuenta_destino` es el numero SINPE
(`8380-8287`) y `cuenta_origen` es el `_id` de la cuenta (`CTA-0176`). El puente es
`cuentas` (`cuenta_sinpe` -> `_id`), y `$graphLookup` no salta de coleccion en cada
nivel. Sin un campo con el SINPE de origen en `transacciones` (o una vista que lo
agregue) no hay `connectFromField`/`connectToField` que conecte un eslabon con el
siguiente. Ademas `$graphLookup` solo filtra con una condicion fija
(`restrictSearchWithMatch`), y aqui la condicion es POR ESLABON: la transferencia que
sigue tiene que salir de la mula despues de que le llego el dinero, dentro de una
ventana. Sin eso, como las mismas mulas se reusan en varios episodios, el recorrido
mezclaria cadenas de dias distintos.

Por eso el recorrido es en anchura, un salto por agregacion, hasta `saltos` (6, el
mismo tope que pediria `maxDepth: 5` en un `$graphLookup`, que cuenta desde 0). Cada
salto es UNA agregacion:

1. `$match` con un `$or` de `{cuenta_origen, fecha en ventana}` por cada cuenta de la
   frontera: lo sirve `idx_transacciones_cuenta_origen_fecha`;
2. `$lookup` a `cuentas` por `cuenta_sinpe` para saber si el destino es una cuenta del
   banco (y seguir) o externa (la cadena sale del banco): lo sirve `idx_cuentas_sinpe`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from pymongo.asynchronous.database import AsyncDatabase

TRANSACCIONES = "transacciones"
CUENTAS = "cuentas"
LISTAS_RIESGO = "listas_riesgo"

SALTOS_MAXIMOS = 6
VENTANA_MINUTOS = 60
# Tope de aristas de una red: una cuenta muy activa como punto de partida, sin `desde`,
# no puede convertir la consulta en un volcado de la coleccion.
MAX_ARISTAS = 500


class CuentaNoEncontrada(LookupError):
    """El punto de partida es un `CTA-...` que no existe en `cuentas`."""


class NodoRed(BaseModel):
    model_config = ConfigDict(frozen=True)

    cuenta: str  # numero SINPE: es lo que ve el agente y lo que guarda listas_riesgo
    cuenta_id: str | None  # None: cuenta externa al banco, sin documento en `cuentas`
    externa: bool
    nivel: int  # 0 = punto de partida; n = primer salto en que aparece
    en_lista_riesgo: bool


class AristaRed(BaseModel):
    model_config = ConfigDict(frozen=True)

    transaccion_id: str
    origen: str
    destino: str
    monto_crc: int
    fecha: datetime
    salto: int
    # Desde que el dinero llego a la cuenta de origen (o desde `desde`, en el salto 1).
    minutos_desde_anterior: float | None


class RedMulas(BaseModel):
    model_config = ConfigDict(frozen=True)

    cuenta: str
    desde: datetime | None
    ventana_minutos: int
    saltos_maximos: int
    saltos_recorridos: int
    truncada: bool
    nodos: list[NodoRed]
    aristas: list[AristaRed]
    propuestas_lista_riesgo: list[str]


def _filtro_salto(
    frontera: list[tuple[str, datetime | None, bool]], ventana: timedelta
) -> dict[str, Any]:
    """`$or` de una clausula por cuenta de la frontera, con su ventana de tiempo."""
    clausulas = []
    for cuenta_id, llegada, inclusiva in frontera:
        clausula: dict[str, Any] = {"cuenta_origen": cuenta_id}
        if llegada is not None:
            clausula["fecha"] = {"$gte" if inclusiva else "$gt": llegada, "$lte": llegada + ventana}
        clausulas.append(clausula)
    return clausulas[0] if len(clausulas) == 1 else {"$or": clausulas}


def pipeline_salto(
    frontera: list[tuple[str, datetime | None, bool]], ventana: timedelta, limite: int
) -> list[dict[str, Any]]:
    """La agregacion de un salto. Publica para poder pedirle `explain` en las pruebas."""
    return [
        {"$match": _filtro_salto(frontera, ventana)},
        {"$sort": {"fecha": 1, "_id": 1}},
        {"$limit": limite},
        {
            "$lookup": {
                "from": CUENTAS,
                "localField": "cuenta_destino",
                "foreignField": "cuenta_sinpe",
                "pipeline": [{"$project": {"_id": 1}}],
                "as": "cuenta_del_destino",
            }
        },
        {
            "$project": {
                "cuenta_origen": 1,
                "cuenta_destino": 1,
                "monto_crc": 1,
                "fecha": 1,
                "destino_cuenta_id": {"$first": "$cuenta_del_destino._id"},
            }
        },
    ]


def _minutos(despues: datetime, antes: datetime | None) -> float | None:
    if antes is None:
        return None
    return round((despues - antes).total_seconds() / 60, 1)


class RepoRedMulas:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def _resolver_inicio(self, cuenta: str) -> tuple[str, str | None]:
        """`(sinpe, cuenta_id)` del punto de partida. Acepta `CTA-...` o un SINPE."""
        if cuenta.startswith("CTA-"):
            documento = await self._base[CUENTAS].find_one({"_id": cuenta}, {"cuenta_sinpe": 1})
            if documento is None:
                raise CuentaNoEncontrada(f"la cuenta {cuenta} no existe")
            return documento["cuenta_sinpe"], cuenta
        documento = await self._base[CUENTAS].find_one({"cuenta_sinpe": cuenta}, {"_id": 1})
        return cuenta, (documento["_id"] if documento is not None else None)

    async def _en_lista(self, cuentas: list[str]) -> set[str]:
        return set(
            await self._base[LISTAS_RIESGO].distinct(
                "cuenta", {"cuenta": {"$in": cuentas}, "vigente": True}
            )
        )

    async def explicar_salto(
        self, frontera: list[tuple[str, datetime | None, bool]], ventana: timedelta
    ) -> dict[str, Any]:
        """`explain` de la agregacion de un salto (para verificar los indices)."""
        return await self._base.command(
            "explain",
            {
                "aggregate": TRANSACCIONES,
                "pipeline": pipeline_salto(frontera, ventana, MAX_ARISTAS),
                "cursor": {},
            },
            verbosity="executionStats",
        )

    async def recorrer(
        self,
        cuenta: str,
        *,
        desde: datetime | None = None,
        ventana_minutos: int = VENTANA_MINUTOS,
        saltos: int = SALTOS_MAXIMOS,
    ) -> RedMulas:
        """La red que sale de `cuenta`, salto por salto, hasta `saltos`.

        En el salto 1 se toman las transferencias de la cuenta desde `desde` (inclusive)
        y dentro de la ventana; sin `desde`, todas las de la cuenta. Desde el salto 2,
        solo las que salen de una cuenta DESPUES de que le llego el dinero y dentro de
        `ventana_minutos`. Cada arista se cuelga de la llegada mas reciente anterior a
        ella: de ahi salen los minutos entre eslabones.
        """
        if not 1 <= saltos <= SALTOS_MAXIMOS:
            raise ValueError(f"saltos debe estar entre 1 y {SALTOS_MAXIMOS}")
        ventana = timedelta(minutes=ventana_minutos)
        sinpe_inicio, id_inicio = await self._resolver_inicio(cuenta)

        nodos: dict[str, dict[str, Any]] = {
            sinpe_inicio: {"cuenta_id": id_inicio, "nivel": 0}
        }
        sinpe_de: dict[str, str] = {id_inicio: sinpe_inicio} if id_inicio is not None else {}
        aristas: list[AristaRed] = []
        vistas: set[str] = set()
        # Llegadas por cuenta_id: (fecha en que llego el dinero, inclusiva). La del
        # punto de partida es `desde`.
        llegadas: dict[str, list[tuple[datetime | None, bool]]] = {}
        if id_inicio is not None:
            llegadas[id_inicio] = [(desde, True)]
        frontera = [(id_inicio, desde, True)] if id_inicio is not None else []
        truncada = False
        recorridos = 0

        for salto in range(1, saltos + 1):
            if not frontera:
                break
            restante = MAX_ARISTAS - len(aristas)
            cursor = await self._base[TRANSACCIONES].aggregate(
                pipeline_salto(frontera, ventana, restante + 1)
            )
            documentos = await cursor.to_list(length=None)
            if len(documentos) > restante:
                documentos = documentos[:restante]
                truncada = True

            siguiente: list[tuple[str, datetime | None, bool]] = []
            for documento in documentos:
                if documento["_id"] in vistas:
                    continue
                vistas.add(documento["_id"])
                origen_id = documento["cuenta_origen"]
                fecha = documento["fecha"]
                anteriores = [
                    llegada
                    for llegada, inclusiva in llegadas.get(origen_id, [])
                    if llegada is None or (llegada <= fecha if inclusiva else llegada < fecha)
                ]
                fechas = [llegada for llegada in anteriores if llegada is not None]
                anterior = max(fechas) if fechas else None
                origen_sinpe = sinpe_de[origen_id]
                destino = documento["cuenta_destino"]
                destino_id = documento.get("destino_cuenta_id")
                aristas.append(
                    AristaRed(
                        transaccion_id=documento["_id"],
                        origen=origen_sinpe,
                        destino=destino,
                        monto_crc=int(documento["monto_crc"]),
                        fecha=fecha,
                        salto=salto,
                        minutos_desde_anterior=_minutos(fecha, anterior),
                    )
                )
                recorridos = salto
                if destino not in nodos:
                    nodos[destino] = {"cuenta_id": destino_id, "nivel": salto}
                if destino_id is not None:
                    sinpe_de.setdefault(destino_id, destino)
                    llegadas.setdefault(destino_id, []).append((fecha, False))
                    siguiente.append((destino_id, fecha, False))
            frontera = siguiente
            if truncada:
                break

        receptoras = sorted(
            {arista.destino for arista in aristas},
            key=lambda sinpe: (nodos[sinpe]["nivel"], sinpe),
        )
        listadas = await self._en_lista(list(nodos))
        return RedMulas(
            cuenta=sinpe_inicio,
            desde=desde,
            ventana_minutos=ventana_minutos,
            saltos_maximos=saltos,
            saltos_recorridos=recorridos,
            truncada=truncada,
            nodos=[
                NodoRed(
                    cuenta=sinpe,
                    cuenta_id=nodo["cuenta_id"],
                    externa=nodo["cuenta_id"] is None,
                    nivel=nodo["nivel"],
                    en_lista_riesgo=sinpe in listadas,
                )
                for sinpe, nodo in sorted(nodos.items(), key=lambda par: (par[1]["nivel"], par[0]))
            ],
            aristas=aristas,
            propuestas_lista_riesgo=[sinpe for sinpe in receptoras if sinpe not in listadas],
        )
