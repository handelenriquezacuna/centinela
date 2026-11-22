"""H-23 · Red de cuentas mula: seguir el dinero desde una cuenta con `$graphLookup`.

Devuelve la red como nodos y aristas (JSON listo para dibujar un grafo) y la lista de
cuentas que se PROPONEN para `listas_riesgo`. No escribe nada: proponer es trabajo del
sistema, decidir es del agente.

**Descubrimiento: un `$graphLookup` sobre `transacciones`.** Cada transaccion lleva
`sinpe_origen` (el SINPE de su cuenta de origen, migracion 002), el mismo espacio de
valores que `cuenta_destino`. Asi un eslabon se une con el siguiente dentro de la misma
coleccion:

    startWith: SINPE de la cuenta de partida
    connectFromField: "cuenta_destino"   (a donde fue el dinero)
    connectToField:   "sinpe_origen"     (las transferencias que salen de ahi)
    maxDepth: saltos - 1                 (profundidad 0 = salto 1; 5 = salto 6)
    depthField: "profundidad"
    restrictSearchWithMatch: fecha en [desde, desde + saltos * ventana]

Cada nivel lo sirve `idx_transacciones_sinpe_origen_fecha` (`{sinpe_origen: 1, fecha:
1}`). La agregacion parte de `cuentas` (`$match` por `idx_cuentas_sinpe`), que es la que
dice si la cuenta de partida es del banco.

**Orden temporal por eslabon, despues.** `restrictSearchWithMatch` es una condicion fija
para todos los niveles; la de una cadena es por eslabon: la transferencia que sigue
tiene que salir de la cuenta DESPUES de que le llego el dinero y dentro de
`ventana_minutos`. Como las mismas mulas se reusan en varios episodios, sin ese filtro
se mezclarian cadenas. `filtrar_por_eslabon` lo aplica en Python sobre lo que devolvio el
`$graphLookup` (unas decenas de documentos con `desde`); es puro y se prueba sin base.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
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
# no puede convertir la respuesta en un volcado de la coleccion.
MAX_ARISTAS = 500
CAMPO_RED = "red"


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


def pipeline_red(
    sinpe_inicio: str, desde: datetime | None, ventana: timedelta, saltos: int
) -> list[dict[str, Any]]:
    """La agregacion sobre `cuentas` con el `$graphLookup` de la red."""
    grafo: dict[str, Any] = {
        "from": TRANSACCIONES,
        "startWith": "$cuenta_sinpe",
        "connectFromField": "cuenta_destino",
        "connectToField": "sinpe_origen",
        "as": CAMPO_RED,
        "maxDepth": saltos - 1,
        "depthField": "profundidad",
    }
    if desde is not None:
        grafo["restrictSearchWithMatch"] = {
            "fecha": {"$gte": desde, "$lte": desde + ventana * saltos}
        }
    return [
        {"$match": {"cuenta_sinpe": sinpe_inicio}},
        {"$graphLookup": grafo},
        {
            "$project": {
                CAMPO_RED: {
                    "_id": 1,
                    "sinpe_origen": 1,
                    "cuenta_destino": 1,
                    "monto_crc": 1,
                    "fecha": 1,
                    "profundidad": 1,
                }
            }
        },
    ]


@dataclass(frozen=True, slots=True)
class Eslabones:
    aristas: list[AristaRed]
    niveles: dict[str, int]  # SINPE -> primer salto en que aparece (0 = partida)
    truncada: bool


def _minutos(despues: datetime, antes: datetime | None) -> float | None:
    if antes is None:
        return None
    return round((despues - antes).total_seconds() / 60, 1)


def filtrar_por_eslabon(
    candidatas: Iterable[Mapping[str, Any]],
    sinpe_inicio: str,
    *,
    desde: datetime | None,
    ventana: timedelta,
    saltos: int,
    max_aristas: int = MAX_ARISTAS,
) -> Eslabones:
    """Recorre en anchura las candidatas del `$graphLookup` con la regla por eslabon.

    Salto 1: las salidas de la partida desde `desde` (inclusive) y dentro de la ventana;
    sin `desde`, todas. Desde el salto 2: las que salen de una cuenta estrictamente
    DESPUES de una llegada y dentro de `ventana` de esa llegada. Cada arista se cuelga de
    la llegada mas reciente anterior a ella: de ahi salen los minutos entre eslabones.
    """
    por_origen: dict[str, list[Mapping[str, Any]]] = {}
    for documento in candidatas:
        por_origen.setdefault(documento["sinpe_origen"], []).append(documento)
    for lista in por_origen.values():
        lista.sort(key=lambda documento: (documento["fecha"], documento["_id"]))

    niveles: dict[str, int] = {sinpe_inicio: 0}
    llegadas: dict[str, list[datetime]] = {}
    aristas: list[AristaRed] = []
    vistas: set[str] = set()
    truncada = False
    # Frontera: (cuenta, llegada, inclusiva). La de la partida es `desde`.
    frontera: list[tuple[str, datetime | None, bool]] = [(sinpe_inicio, desde, True)]

    for salto in range(1, saltos + 1):
        siguiente: list[tuple[str, datetime | None, bool]] = []
        elegidas: list[Mapping[str, Any]] = []
        for cuenta, llegada, inclusiva in frontera:
            for documento in por_origen.get(cuenta, []):
                fecha = documento["fecha"]
                if documento["_id"] in vistas:
                    continue
                if llegada is not None:
                    despues = fecha >= llegada if inclusiva else fecha > llegada
                    if not despues or fecha > llegada + ventana:
                        continue
                vistas.add(documento["_id"])
                elegidas.append(documento)
        elegidas.sort(key=lambda documento: (documento["fecha"], documento["_id"]))

        for documento in elegidas:
            if len(aristas) >= max_aristas:
                truncada = True
                break
            origen = documento["sinpe_origen"]
            fecha = documento["fecha"]
            previas = [llegada for llegada in llegadas.get(origen, []) if llegada < fecha]
            anterior = max(previas) if previas else (desde if origen == sinpe_inicio else None)
            destino = documento["cuenta_destino"]
            aristas.append(
                AristaRed(
                    transaccion_id=documento["_id"],
                    origen=origen,
                    destino=destino,
                    monto_crc=int(documento["monto_crc"]),
                    fecha=fecha,
                    salto=salto,
                    minutos_desde_anterior=_minutos(fecha, anterior),
                )
            )
            niveles.setdefault(destino, salto)
            llegadas.setdefault(destino, []).append(fecha)
            siguiente.append((destino, fecha, False))
        frontera = siguiente
        if truncada or not frontera:
            break

    return Eslabones(aristas=aristas, niveles=niveles, truncada=truncada)


class RepoRedMulas:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def _resolver_inicio(self, cuenta: str) -> str:
        """SINPE del punto de partida. Acepta `CTA-...` o un SINPE."""
        if not cuenta.startswith("CTA-"):
            return cuenta
        documento = await self._base[CUENTAS].find_one({"_id": cuenta}, {"cuenta_sinpe": 1})
        if documento is None:
            raise CuentaNoEncontrada(f"la cuenta {cuenta} no existe")
        return documento["cuenta_sinpe"]

    async def _cuentas_del_banco(self, sinpes: list[str]) -> dict[str, str]:
        return {
            documento["cuenta_sinpe"]: documento["_id"]
            async for documento in self._base[CUENTAS].find(
                {"cuenta_sinpe": {"$in": sinpes}}, {"cuenta_sinpe": 1}
            )
        }

    async def _en_lista(self, cuentas: list[str]) -> set[str]:
        return set(
            await self._base[LISTAS_RIESGO].distinct(
                "cuenta", {"cuenta": {"$in": cuentas}, "vigente": True}
            )
        )

    async def explicar(
        self,
        cuenta: str,
        *,
        desde: datetime | None = None,
        ventana_minutos: int = VENTANA_MINUTOS,
        saltos: int = SALTOS_MAXIMOS,
    ) -> dict[str, Any]:
        """`explain` (executionStats) de la agregacion del `$graphLookup`."""
        sinpe = await self._resolver_inicio(cuenta)
        return await self._base.command(
            "explain",
            {
                "aggregate": CUENTAS,
                "pipeline": pipeline_red(sinpe, desde, timedelta(minutes=ventana_minutos), saltos),
                "cursor": {},
            },
            verbosity="executionStats",
        )

    async def usos_de_indices(self) -> dict[str, int]:
        """Accesos acumulados por indice de `transacciones` (`$indexStats`).

        El `explain` de un `$graphLookup` no dice que indice usan sus busquedas por
        nivel (solo las etapas de cursor lo reportan). El contador de `$indexStats` si:
        si sube el de `idx_transacciones_sinpe_origen_fecha` al recorrer, cada nivel
        fue por indice.
        """
        cursor = await self._base[TRANSACCIONES].aggregate([{"$indexStats": {}}])
        return {
            estadistica["name"]: int(estadistica["accesses"]["ops"])
            for estadistica in await cursor.to_list(length=None)
        }

    async def recorrer(
        self,
        cuenta: str,
        *,
        desde: datetime | None = None,
        ventana_minutos: int = VENTANA_MINUTOS,
        saltos: int = SALTOS_MAXIMOS,
    ) -> RedMulas:
        """La red que sale de `cuenta`, hasta `saltos`. Ver la cabecera del modulo."""
        if not 1 <= saltos <= SALTOS_MAXIMOS:
            raise ValueError(f"saltos debe estar entre 1 y {SALTOS_MAXIMOS}")
        ventana = timedelta(minutes=ventana_minutos)
        sinpe_inicio = await self._resolver_inicio(cuenta)

        cursor = await self._base[CUENTAS].aggregate(
            pipeline_red(sinpe_inicio, desde, ventana, saltos)
        )
        resultado = await cursor.to_list(length=1)
        # Sin documento: la partida es externa al banco, no tiene salidas registradas.
        candidatas = resultado[0][CAMPO_RED] if resultado else []
        eslabones = filtrar_por_eslabon(
            candidatas, sinpe_inicio, desde=desde, ventana=ventana, saltos=saltos
        )

        sinpes = list(eslabones.niveles)
        del_banco = await self._cuentas_del_banco(sinpes)
        listadas = await self._en_lista(sinpes)
        receptoras = sorted(
            {arista.destino for arista in eslabones.aristas},
            key=lambda sinpe: (eslabones.niveles[sinpe], sinpe),
        )
        return RedMulas(
            cuenta=sinpe_inicio,
            desde=desde,
            ventana_minutos=ventana_minutos,
            saltos_maximos=saltos,
            saltos_recorridos=max((arista.salto for arista in eslabones.aristas), default=0),
            truncada=eslabones.truncada,
            nodos=[
                NodoRed(
                    cuenta=sinpe,
                    cuenta_id=del_banco.get(sinpe),
                    externa=sinpe not in del_banco,
                    nivel=nivel,
                    en_lista_riesgo=sinpe in listadas,
                )
                for sinpe, nivel in sorted(
                    eslabones.niveles.items(), key=lambda par: (par[1], par[0])
                )
            ],
            aristas=eslabones.aristas,
            propuestas_lista_riesgo=[sinpe for sinpe in receptoras if sinpe not in listadas],
        )
