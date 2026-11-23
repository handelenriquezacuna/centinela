"""H-20 · Lectura de `indicadores_diarios` para el tablero.

El tablero NO calcula: lee el rollup que materializa H-22 (un documento por dia, `_id` =
la fecha `YYYY-MM-DD`) y lo suma por periodo. La unica consulta es una lectura por rango
de `_id`, que ya tiene indice. La marca de tiempo del ultimo calculo es `calculado_en`
del rollup, no la hora de la peticion.

Periodo: los ultimos `dias` dias CON rollup contados hacia atras desde el dia mas reciente
del que hay datos, y el periodo anterior son los `dias` dias previos a ese. Se ancla en
el ultimo rollup y no en el reloj para que un rollup viejo no deje el tablero en cero.

`por_agente` (agente_id -> cantidad) y `por_hora` ("00".."23", hora de Costa Rica ->
cantidad) son opcionales: el validador de la semilla todavia no los declara. Si el
rollup no los trae, el tablero lo dice en vez de inventarlos.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Protocol

from pymongo.asynchronous.database import AsyncDatabase

INDICADORES = "indicadores_diarios"
DIAS_MAXIMOS = 30
SEVERIDADES = ("CRITICA", "ALTA", "MEDIA")
ESTADOS = ("nueva", "en_revision", "confirmada", "descartada", "escalada")


class ProtocoloRepoTablero(Protocol):
    async def leer(self, dias: int) -> dict[str, Any]: ...


def _sumar(documentos: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0
    severidad = dict.fromkeys(SEVERIDADES, 0)
    estado = dict.fromkeys(ESTADOS, 0)
    agentes: dict[str, int] = {}
    horas: dict[str, int] = {}
    con_agentes = con_horas = False
    for documento in documentos:
        total += int(documento.get("total_alertas", 0))
        for clave in SEVERIDADES:
            severidad[clave] += int(documento.get("por_severidad", {}).get(clave, 0))
        for clave in ESTADOS:
            estado[clave] += int(documento.get("por_estado", {}).get(clave, 0))
        if "por_agente" in documento:
            con_agentes = True
            for agente, cantidad in documento["por_agente"].items():
                agentes[agente] = agentes.get(agente, 0) + int(cantidad)
        if "por_hora" in documento:
            con_horas = True
            for hora, cantidad in documento["por_hora"].items():
                horas[hora] = horas.get(hora, 0) + int(cantidad)
    return {
        "total": total,
        "por_severidad": severidad,
        "por_estado": estado,
        "por_agente": agentes if con_agentes else None,
        "por_hora": {f"{h:02d}": horas.get(f"{h:02d}", 0) for h in range(24)}
        if con_horas
        else None,
        "dias_con_datos": len(documentos),
    }


def _variacion(actual: int, previo: int) -> dict[str, Any]:
    return {
        "actual": actual,
        "previo": previo,
        "delta": actual - previo,
        # Sin base de comparacion no hay porcentaje: dividir por cero seria inventar.
        "porcentaje": round((actual - previo) * 100 / previo, 1) if previo else None,
    }


def resumir(documentos: list[dict[str, Any]], dias: int) -> dict[str, Any]:
    """Suma el periodo actual y el anterior y los compara. Puro: se prueba sin base."""
    if not documentos:
        return {"vacio": True, "dias": dias}
    ultimo = max(date.fromisoformat(d["fecha"]) for d in documentos)
    inicio_actual = ultimo - timedelta(days=dias - 1)
    inicio_previo = inicio_actual - timedelta(days=dias)
    actual = [d for d in documentos if date.fromisoformat(d["fecha"]) >= inicio_actual]
    previo = [
        d
        for d in documentos
        if inicio_previo <= date.fromisoformat(d["fecha"]) < inicio_actual
    ]
    a, p = _sumar(actual), _sumar(previo)
    marcas = [d["calculado_en"] for d in documentos if isinstance(d.get("calculado_en"), datetime)]
    return {
        "vacio": False,
        "dias": dias,
        "periodo_actual": {"desde": inicio_actual.isoformat(), "hasta": ultimo.isoformat()},
        "periodo_previo": {
            "desde": inicio_previo.isoformat(),
            "hasta": (inicio_actual - timedelta(days=1)).isoformat(),
        },
        "calculado_en": max(marcas) if marcas else None,
        "actual": a,
        "previo": p,
        "total": _variacion(a["total"], p["total"]),
        "severidad": {k: _variacion(a["por_severidad"][k], p["por_severidad"][k]) for k in SEVERIDADES},
        "estado": {k: _variacion(a["por_estado"][k], p["por_estado"][k]) for k in ESTADOS},
        "hay_periodo_previo": p["dias_con_datos"] > 0,
    }


class RepoTablero:
    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base

    async def leer(self, dias: int) -> dict[str, Any]:
        if not 1 <= dias <= DIAS_MAXIMOS:
            raise ValueError(f"dias debe estar entre 1 y {DIAS_MAXIMOS}")
        coleccion = self._base[INDICADORES]
        ultimo = await coleccion.find_one({}, {"fecha": 1}, sort=[("_id", -1)])
        if ultimo is None:
            return resumir([], dias)
        inicio = date.fromisoformat(ultimo["fecha"]) - timedelta(days=2 * dias - 1)
        # Rango por `_id` (la fecha como texto ordena igual que como fecha), tope 2 * dias.
        cursor = coleccion.find({"_id": {"$gte": inicio.isoformat()}}).sort("_id", 1)
        documentos = await cursor.to_list(length=2 * DIAS_MAXIMOS)
        return resumir(documentos, dias)
