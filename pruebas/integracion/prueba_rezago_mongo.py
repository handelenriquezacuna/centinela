"""T-57 · Rezago de D1 contra MongoDB de verdad, y su plan con `explain`.

Base propia (`antifraude_pruebas_d1`, la de la prueba de D1). Se usa el repositorio real:
el rezago es "transacciones con `_id` posterior a `ultima_transaccion_id` del token".

El plan se justifica con `explain("executionStats")`:

- contar los pendientes recorre claves del indice de `_id` y no lee un documento
  (`totalDocsExamined` 0), aun con la demo de tres digitos mezclada en la coleccion;
- la mas vieja es leer un documento (`nReturned` 1, `totalDocsExamined` 1), sin importar
  cuantos pendientes haya.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.deteccion.d1 import NOMBRE_D1, d1
from app.generador.ejecucion import ejecutar
from app.modelos import Transaccion
from app.nucleo.config import AjustesDeteccion, AjustesGenerador
from app.repos.deteccion import RepoDeteccion, filtro_pendientes
from app.repos.generador import RepoGenerador
from app.repos.ids_transaccion import siguiente_numero_transaccion
from app.supervision import Supervisor
from pruebas.dobles.datos_demo import TRANSACCIONES
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
    await _insertar(base_d1, [*TRANSACCIONES, *_lote(1, 3000)])  # TXN-001..007 y 3000
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
    # Las 7 de tres digitos caen dentro del rango de texto (`TXN-001 > TXN-000500`): se
    # recorren sus claves y el patron anclado las descarta antes de leer un documento.
    assert estadisticas_conteo["totalKeysExamined"] == 2507
    assert estadisticas_conteo["nReturned"] == 1  # el $group: una sola fila, el conteo
    assert await base_d1["transacciones"].count_documents(filtro) == 2500
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


# ---------------------------------------------------------------------------
# T-69: tres familias de ids, un solo rezago
# ---------------------------------------------------------------------------

AJUSTES_RAPIDOS = AjustesDeteccion(
    espera_cursor_ms=100,
    reintentos=1,
    espera_inicial_segundos=0.05,
    espera_maxima_segundos=0.2,
    rezago_intervalo_segundos=0.2,
)


@pytest.mark.asyncio
async def prueba_la_carga_masiva_tras_procesar_la_demo_se_ve_como_rezago(base_d1: Any) -> None:  # noqa: F811
    """El fallo original: D1 termino TXN-007 y `TXN-000500 > TXN-007` es falso, asi que
    600 pendientes del generador se publicaban como 0."""
    repo = RepoDeteccion(base_d1)
    await _insertar(base_d1, TRANSACCIONES)  # TXN-001..007, la demo sembrada
    await repo.guardar_token(NOMBRE_D1, TOKEN_FALSO, transaccion_id="TXN-007")

    resumen = await ejecutar(
        RepoGenerador(base_d1), AjustesGenerador(clientes=20, transacciones=600)
    )
    medido = await repo.medir_rezago(NOMBRE_D1)

    assert resumen.transacciones == 600
    assert await base_d1["transacciones"].find_one({"_id": "TXN-000001"}) is not None
    assert medido.pendientes == 600, "la demo de tres digitos no cuenta; las 600 si"
    assert medido.mas_vieja is not None


@pytest.mark.asyncio
async def prueba_una_insercion_manual_entre_dos_cargas_sigue_contando(base_d1: Any) -> None:  # noqa: F811
    """La demo de la defensa: inserta a mano, luego carga masiva. El asignador continua
    despues del mayor de seis digitos, y lo manual y lo generado son pendientes."""
    repo = RepoDeteccion(base_d1)
    await _insertar(base_d1, TRANSACCIONES)
    await ejecutar(RepoGenerador(base_d1), AjustesGenerador(clientes=20, transacciones=600))
    await repo.guardar_token(NOMBRE_D1, TOKEN_FALSO, transaccion_id="TXN-000600")
    assert (await repo.medir_rezago(NOMBRE_D1)).pendientes == 0

    primero = await siguiente_numero_transaccion(base_d1)
    assert primero == 601
    manuales = [_noche(1 + n, f"TXN-{primero + n:06d}", f"7200-{n:04d}") for n in range(3)]
    await _insertar(base_d1, manuales)
    assert (await repo.medir_rezago(NOMBRE_D1)).pendientes == 3

    # Segunda carga masiva: limpia lo generado (1..600) y continua tras lo manual (603).
    await ejecutar(RepoGenerador(base_d1), AjustesGenerador(clientes=20, transacciones=600))
    ids = sorted(
        d["_id"]
        for d in await base_d1["transacciones"]
        .find({"simulacion": {"$exists": True}}, {"_id": 1})
        .to_list(length=None)
    )
    medido = await repo.medir_rezago(NOMBRE_D1)

    assert ids[0] == "TXN-000604" and ids[-1] == "TXN-001203"
    assert medido.pendientes == 603
    assert medido.mas_vieja == manuales[0].fecha


@pytest.mark.asyncio
async def prueba_d1_publica_el_rezago_y_el_atraso_durante_una_carga_masiva(base_d1: Any) -> None:  # noqa: F811
    """Con D1 corriendo: procesa TXN-007, entran 600 del generador, el rezago publicado
    es mayor que cero y el atraso en segundos tambien; al terminar, ambos vuelven a 0."""
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(RepoDeteccion(base_d1), supervisor, ajustes=AJUSTES_RAPIDOS))
    try:

        async def abierto() -> bool:
            return supervisor.informe().tarea(NOMBRE_D1).eventos_procesados >= 1

        await _esperar(abierto, "el primer latido de D1")
        await _insertar(base_d1, [TRANSACCIONES[-1]])  # TXN-007
        await _procesada_hasta(base_d1, "TXN-007")

        await ejecutar(RepoGenerador(base_d1), AjustesGenerador(clientes=20, transacciones=600))

        maximo_pendientes = 0
        maximo_atraso = 0.0

        async def al_dia() -> bool:
            nonlocal maximo_pendientes, maximo_atraso
            medida = supervisor.informe().tarea(NOMBRE_D1).rezago
            if medida is None:
                return False
            maximo_pendientes = max(maximo_pendientes, medida.pendientes)
            maximo_atraso = max(maximo_atraso, medida.atraso_segundos or 0.0)
            estado = await base_d1["deteccion_estado"].find_one({"_id": NOMBRE_D1})
            return (
                estado is not None
                and estado.get("ultima_transaccion_id") == "TXN-000600"
                and medida.pendientes == 0
                and medida.atraso_segundos == 0.0
            )

        await _esperar(al_dia, "que D1 termine las 600 y publique rezago y atraso en 0")
    finally:
        await supervisor.detener()

    assert maximo_pendientes > 0, "el rezago publicado nunca vio las pendientes"
    assert maximo_atraso > 0.0
