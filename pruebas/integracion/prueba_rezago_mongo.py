"""T-57 · Rezago de D1 contra MongoDB de verdad, y su plan con `explain`.

Base propia (`antifraude_pruebas_d1`, la de la prueba de D1). Se usa el repositorio real:
el rezago es "transacciones con `_id` posterior a `ultima_transaccion_id` del token".

El plan se justifica con `explain("executionStats")`:

- contar los pendientes recorre claves del indice de `_id` y no lee un documento
  (`totalDocsExamined` 0);
- la mas vieja es leer un documento (`nReturned` 1, `totalDocsExamined` 1), sin importar
  cuantos pendientes haya.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.deteccion.d1 import NOMBRE_D1
from app.modelos import Transaccion
from app.repos.deteccion import RepoDeteccion, filtro_pendientes
from pruebas.integracion.prueba_d1_mongo import (  # noqa: F401 - base_d1 es un fixture
    _d1_corriendo,
    _esperar,
    _insertar,
    _noche,
    _procesada_hasta,
    base_d1,
)

pytestmark = pytest.mark.mongo

TOKEN_FALSO = {"_data": "8200000000"}


def _lote(desde: int, cuantas: int) -> list[Transaccion]:
    return [
        _noche(1 + (desde + i) % 27, f"TXN-{desde + i:06d}", f"7100-{desde + i:04d}")
        for i in range(cuantas)
    ]


@pytest.mark.asyncio
async def prueba_sin_token_el_rezago_es_cero(base_d1: Any) -> None:  # noqa: F811
    await _insertar(base_d1, _lote(1, 5))

    medido = await RepoDeteccion(base_d1).medir_rezago(NOMBRE_D1)

    assert (medido.pendientes, medido.mas_vieja) == (0, None)


@pytest.mark.asyncio
async def prueba_cuenta_lo_posterior_a_la_ultima_procesada(base_d1: Any) -> None:  # noqa: F811
    repo = RepoDeteccion(base_d1)
    lote = _lote(1, 10)
    await _insertar(base_d1, lote)
    await repo.guardar_token(NOMBRE_D1, TOKEN_FALSO, transaccion_id="TXN-000004")

    medido = await repo.medir_rezago(NOMBRE_D1)

    assert medido.pendientes == 6
    assert medido.mas_vieja == lote[4].fecha  # TXN-000005, la primera sin evaluar
    assert medido.mas_vieja is not None and medido.mas_vieja.tzinfo is UTC

    await repo.guardar_token(NOMBRE_D1, TOKEN_FALSO, transaccion_id="TXN-000010")
    al_dia = await repo.medir_rezago(NOMBRE_D1)
    assert (al_dia.pendientes, al_dia.mas_vieja) == (0, None)


def _etapas(plan: Any) -> list[str]:
    """Nombres de etapa del plan ganador, de la raiz a la hoja."""
    nombres: list[str] = []
    plan = plan.get("queryPlan", plan)  # el motor SBE envuelve el plan en `queryPlan`
    while plan:
        nombres.append(plan["stage"])
        plan = plan.get("inputStage") or (plan.get("inputStages") or [None])[0]
    return nombres


@pytest.mark.asyncio
async def prueba_el_plan_del_rezago_usa_el_indice_y_no_lee_documentos(
    base_d1: Any,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _insertar(base_d1, _lote(1, 3000))
    filtro = filtro_pendientes("TXN-000500")

    conteo = await base_d1.command(
        {
            "explain": {
                "aggregate": "transacciones",
                "pipeline": [{"$match": filtro}, {"$group": {"_id": 1, "n": {"$sum": 1}}}],
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
    )
    primera = await base_d1.command(
        {
            "explain": {
                "find": "transacciones",
                "filter": filtro,
                "projection": {"fecha": 1},
                "sort": {"_id": 1},
                "limit": 1,
            },
            "verbosity": "executionStats",
        }
    )

    # En un aggregate el plan de la parte $match vive en la etapa `$cursor`; en una
    # coleccion sin particionar tambien puede venir en la raiz.
    cursor = conteo["stages"][0]["$cursor"] if "stages" in conteo else conteo
    estadisticas_conteo = cursor["executionStats"]
    estadisticas_primera = primera["executionStats"]
    with capsys.disabled():
        print(
            "\n[rezago explain] conteo: "
            f"keysExamined={estadisticas_conteo['totalKeysExamined']} "
            f"totalDocsExamined={estadisticas_conteo['totalDocsExamined']} | "
            "mas vieja: "
            f"nReturned={estadisticas_primera['nReturned']} "
            f"keysExamined={estadisticas_primera['totalKeysExamined']} "
            f"totalDocsExamined={estadisticas_primera['totalDocsExamined']}"
        )

    # COUNT_SCAN: el conteo sobre el indice de `_id`, sin tocar documentos.
    assert {"IXSCAN", "COUNT_SCAN"} & set(_etapas(cursor["queryPlanner"]["winningPlan"]))
    assert estadisticas_conteo["totalDocsExamined"] == 0
    assert estadisticas_conteo["totalKeysExamined"] == 2500
    assert "IXSCAN" in _etapas(primera["queryPlanner"]["winningPlan"])
    assert "SORT" not in _etapas(primera["queryPlanner"]["winningPlan"])
    assert estadisticas_primera["nReturned"] == 1
    assert estadisticas_primera["totalDocsExamined"] == 1


@pytest.mark.asyncio
async def prueba_d1_publica_el_rezago_y_llega_a_cero(base_d1: Any) -> None:  # noqa: F811
    """Con D1 corriendo, la cifra de `/estado` baja a 0 cuando termina de evaluar."""
    async with _d1_corriendo(base_d1) as supervisor:
        lote = _lote(1, 30)
        await _insertar(base_d1, lote)
        await _procesada_hasta(base_d1, lote[-1].transaccion_id)

        async def en_cero() -> bool:
            medida = supervisor.informe().tarea(NOMBRE_D1).rezago
            return medida is not None and medida.pendientes == 0

        await _esperar(en_cero, "que el rezago publicado llegue a 0")
        medida = supervisor.informe().tarea(NOMBRE_D1).rezago

    assert medida is not None
    assert medida.antiguedad_segundos is None
    assert isinstance(medida.medido_en, datetime)
