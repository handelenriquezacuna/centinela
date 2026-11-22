"""H-23 · La red de mulas sin base: el pipeline que arma el repo y el filtro por eslabon.

La primera prueba es la guardia de la Unidad 10: si `recorrer` deja de mandar un
`$graphLookup` (por ejemplo, vuelve a un salto por agregacion), falla. Mira lo que el
repositorio le pasa de verdad a `aggregate`, no una funcion auxiliar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.repos.red_mulas import SALTOS_MAXIMOS, RepoRedMulas, filtrar_por_eslabon

T0 = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
VENTANA = timedelta(minutes=60)


def _txn(txn_id: str, origen: str, destino: str, minutos: int, monto: int = 1000) -> dict:
    return {
        "_id": txn_id,
        "sinpe_origen": origen,
        "cuenta_destino": destino,
        "monto_crc": monto,
        "fecha": T0 + timedelta(minutes=minutos),
    }


class _Cursor:
    def __init__(self, documentos: list[dict]) -> None:
        self._documentos = documentos

    async def to_list(self, length: int | None = None) -> list[dict]:
        return self._documentos

    def __aiter__(self) -> Any:
        async def generar() -> Any:
            for documento in self._documentos:
                yield documento

        return generar()


class _Coleccion:
    def __init__(self, base: _Base, nombre: str) -> None:
        self._base = base
        self._nombre = nombre

    async def find_one(self, filtro: dict, proyeccion: Any = None) -> dict | None:
        return {"_id": filtro["_id"], "cuenta_sinpe": "1111-0001"}

    def find(self, filtro: dict, proyeccion: Any = None) -> _Cursor:
        return _Cursor([{"_id": "CTA-0001", "cuenta_sinpe": "1111-0001"}])

    async def distinct(self, campo: str, filtro: dict) -> list[str]:
        return []

    async def aggregate(self, pipeline: list[dict]) -> _Cursor:
        self._base.pipelines.append((self._nombre, pipeline))
        return _Cursor(
            [{"_id": "CTA-0001", "red": [_txn("TXN-000001", "1111-0001", "2222-0002", 5)]}]
        )


class _Base:
    def __init__(self) -> None:
        self.pipelines: list[tuple[str, list[dict]]] = []

    def __getitem__(self, nombre: str) -> _Coleccion:
        return _Coleccion(self, nombre)


@pytest.mark.asyncio
async def prueba_recorrer_manda_un_graphlookup_sobre_transacciones() -> None:
    base = _Base()

    red = await RepoRedMulas(base).recorrer("CTA-0001", desde=T0)  # type: ignore[arg-type]

    assert len(base.pipelines) == 1, "una sola agregacion: el $graphLookup, no un bucle"
    coleccion, pipeline = base.pipelines[0]
    assert coleccion == "cuentas"
    grafos = [etapa["$graphLookup"] for etapa in pipeline if "$graphLookup" in etapa]
    assert len(grafos) == 1, pipeline
    grafo = grafos[0]
    assert grafo["from"] == "transacciones"
    assert grafo["connectFromField"] == "cuenta_destino"
    assert grafo["connectToField"] == "sinpe_origen"
    assert grafo["maxDepth"] == SALTOS_MAXIMOS - 1
    assert grafo["depthField"]
    assert grafo["restrictSearchWithMatch"] == {
        "fecha": {"$gte": T0, "$lte": T0 + VENTANA * SALTOS_MAXIMOS}
    }
    assert [arista.transaccion_id for arista in red.aristas] == ["TXN-000001"]


def prueba_el_filtro_no_mezcla_episodios_que_reusan_la_mula() -> None:
    """La mula M recibe a las 0 y a las 300 min; cada salida se cuelga de su llegada.

    La salida de las 320 no pertenece a la cadena que entro a las 0 (fuera de ventana),
    y la de las 5 de M sale despues de la llegada: entra.
    """
    candidatas = [
        _txn("TXN-1", "VICT-0001", "MULA-0001", 0, 100_000),
        _txn("TXN-2", "MULA-0001", "SALE-0001", 5, 95_000),
        _txn("TXN-3", "OTRA-0001", "MULA-0001", 300, 50_000),  # otro episodio
        _txn("TXN-4", "MULA-0001", "SALE-0002", 320, 48_000),
    ]

    red = filtrar_por_eslabon(
        candidatas, "VICT-0001", desde=T0, ventana=VENTANA, saltos=SALTOS_MAXIMOS
    )

    assert [(a.transaccion_id, a.salto) for a in red.aristas] == [("TXN-1", 1), ("TXN-2", 2)]
    assert red.aristas[1].minutos_desde_anterior == 5.0


def prueba_una_salida_anterior_a_la_llegada_no_es_eslabon() -> None:
    candidatas = [
        _txn("TXN-1", "VICT-0001", "MULA-0001", 10),
        _txn("TXN-0", "MULA-0001", "SALE-0001", 9),  # antes de que llegue el dinero
    ]

    red = filtrar_por_eslabon(
        candidatas, "VICT-0001", desde=T0, ventana=VENTANA, saltos=SALTOS_MAXIMOS
    )

    assert [a.transaccion_id for a in red.aristas] == ["TXN-1"]


def prueba_respeta_el_tope_de_saltos_y_de_aristas() -> None:
    cadena = [_txn(f"TXN-{i}", f"C-{i}", f"C-{i + 1}", i) for i in range(8)]

    corta = filtrar_por_eslabon(cadena, "C-0", desde=T0, ventana=VENTANA, saltos=3)
    tope = filtrar_por_eslabon(
        cadena, "C-0", desde=T0, ventana=VENTANA, saltos=6, max_aristas=2
    )

    assert [a.salto for a in corta.aristas] == [1, 2, 3]
    assert not corta.truncada
    assert len(tope.aristas) == 2 and tope.truncada
