"""T-56 · Casos por cuenta destino contra Mongo de verdad.

Base propia (`antifraude_pruebas_casos`, T-34: ninguna prueba lee `antifraude`): validadores
e indices reales de 01 y 02. Se afirma:

1. el `$group` de `RepoCasosDestino` da lo mismo que `agrupar_por_destino`, la funcion pura
   que usa el doble (se comparan los dos caminos sobre los mismos datos);
2. el plan (explain): la agregacion entra por `idx_alertas_estado_puntaje_fecha` y lee SOLO
   las abiertas, y las lee todas para el grupo: `nReturned == totalDocsExamined` y menos
   que el total de la coleccion; el caso de una cuenta entra por `idx_alertas_destino_estado`;
3. el lote contra la base real: cada alerta pasa una por una por la maquina de estados, el
   409 de una no frena a las demas y el historial queda escrito.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio

from app.api.rutas.alertas import aplicar_lote
from app.modelos import EstadoAlerta
from app.nucleo.config import Ajustes
from app.nucleo.db import ciclo_cliente
from app.repos.alertas import (
    CasoDeLote,
    ConflictoAlerta,
    RepoAlertas,
    planear_transicion,
)
from app.repos.carga_demo import a_entero_64, cargar_demo
from app.repos.casos_destino import (
    RepoCasosDestino,
    agrupar_por_destino,
    pipeline_casos,
)
from app.seguridad.tokens import Sesion
from pruebas.conftest import MENSAJE_SIN_MONGO, RUTA_RAIZ, saltar_o_fallar
from pruebas.dobles.casos_demo import MULA, alerta_de_caso, caso_de_la_mula
from pruebas.dobles.datos_demo import LISTAS_RIESGO

pytestmark = pytest.mark.mongo

BASE_CASOS = "antifraude_pruebas_casos"
RELLENO = 600  # alertas de otros destinos, la mitad cerradas: el plan tiene que ignorarlas

_JS = """
const destino = db.getMongo().getDB("%(base)s");
destino.dropDatabase();
db.getSiblingDB = function () { return destino; };
destino.getSiblingDB = function () { return destino; };
print = function () {};
load("%(semilla)s/01-colecciones.js");
load("%(semilla)s/02-indices.js");
destino.alertas.deleteMany({});
destino.listas_riesgo.deleteMany({});
"""


def _relleno() -> list[dict[str, Any]]:
    estados = [EstadoAlerta.NUEVA, EstadoAlerta.CONFIRMADA, EstadoAlerta.DESCARTADA]
    return [
        alerta_de_caso(
            1000 + n,
            destino=f"70{n % 20:02d}-1000",
            estado=estados[n % 3],
            agente_id="AGT-001" if estados[n % 3] != EstadoAlerta.NUEVA else None,
            minuto=n,
            puntaje=40 + n % 30,
        )
        for n in range(RELLENO)
    ]


@pytest_asyncio.fixture
async def base_casos() -> AsyncIterator[Any]:
    import asyncio
    import shutil
    import subprocess

    ajustes = Ajustes()
    async with ciclo_cliente(ajustes) as cliente:
        try:
            await cliente.admin.command("ping")
        except Exception:  # noqa: BLE001 - cualquier falla de conexion es "no responde"
            saltar_o_fallar(MENSAJE_SIN_MONGO)
        mongosh = shutil.which("mongosh")
        if mongosh is None:
            saltar_o_fallar("mongosh no esta en el PATH; hace falta para 01 y 02")
        js = _JS % {"base": BASE_CASOS, "semilla": str(RUTA_RAIZ / "scripts" / "semilla")}
        resultado = await asyncio.to_thread(
            subprocess.run,
            [mongosh, ajustes.mongo.uri, "--quiet", "--eval", js],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if resultado.returncode != 0:
            saltar_o_fallar(f"no se pudo sembrar {BASE_CASOS}: {resultado.stdout}{resultado.stderr}")
        base = cliente[BASE_CASOS]
        await cargar_demo(
            base,
            {
                "alertas": [],
                "listas_riesgo": [LISTAS_RIESGO[0]],
            },
        )
        documentos = [*caso_de_la_mula(), *_relleno()]
        await base["alertas"].insert_many([a_entero_64(d) for d in documentos])
        try:
            yield base
        finally:
            await cliente.drop_database(BASE_CASOS)


def _como_comparable(caso: Any) -> dict[str, Any]:
    datos = dict(vars(caso))
    datos["alertas"] = [
        {**a, "fecha_creacion": a["fecha_creacion"].astimezone(UTC)} for a in caso.alertas
    ]
    return datos


@pytest.mark.asyncio
async def prueba_el_group_da_lo_mismo_que_la_agrupacion_pura(base_casos: Any) -> None:
    repo = RepoCasosDestino(base_casos)
    documentos = await base_casos["alertas"].find().to_list(length=None)
    esperados = agrupar_por_destino(documentos, en_riesgo={MULA})

    obtenidos, total = await repo.listar(limite=1000)

    assert total == len(esperados) == len(obtenidos)
    assert obtenidos[0].cuenta_destino == MULA  # critica de 75 contra relleno de hasta 69
    for real, puro in zip(obtenidos, esperados, strict=True):
        real.sugerencia = puro.sugerencia = None
        assert _como_comparable(real) == _como_comparable(puro)
    mula = obtenidos[0]
    assert mula.cuenta_destino == MULA and mula.en_lista_riesgo
    assert (mula.total_alertas, mula.victimas, mula.monto_total_crc) == (3, 3, 520_000)
    assert isinstance(mula.monto_total_crc, int)


@pytest.mark.asyncio
async def prueba_un_caso_y_la_pagina_con_tope(base_casos: Any) -> None:
    repo = RepoCasosDestino(base_casos)
    uno = await repo.obtener(MULA)
    assert uno is not None and uno.total_alertas == 3
    assert await repo.obtener("9999-0000") is None
    pagina, total = await repo.listar(limite=5)
    assert len(pagina) == 5 and total > 5
    siguiente, _ = await repo.listar(limite=5, desplazamiento=5)
    assert {c.cuenta_destino for c in pagina}.isdisjoint(c.cuenta_destino for c in siguiente)


async def _explicar(base: Any, etapas: list[dict[str, Any]]) -> tuple[str, int, int, int, bool]:
    respuesta = await base.command(
        {
            "explain": {"aggregate": "alertas", "pipeline": etapas, "cursor": {}},
            "verbosity": "executionStats",
        }
    )
    cursor = respuesta["stages"][0]["$cursor"]
    plan = cursor["queryPlanner"]["winningPlan"]
    etapas_plan = []
    pendientes = [plan]
    while pendientes:
        actual = pendientes.pop()
        if not actual:
            continue
        etapas_plan.append(actual)
        pendientes.extend([actual.get("inputStage"), *actual.get("inputStages", [])])
    indice = next((e["indexName"] for e in etapas_plan if e["stage"] == "IXSCAN"), "COLLSCAN")
    hay_sort = any(e["stage"] == "SORT" for e in etapas_plan)
    stats = cursor["executionStats"]
    print(
        f"\nindice={indice} nReturned={stats['nReturned']} "
        f"totalDocsExamined={stats['totalDocsExamined']} sort={hay_sort}"
    )
    return indice, stats["nReturned"], stats["totalDocsExamined"], stats["totalKeysExamined"], hay_sort


@pytest.mark.asyncio
async def prueba_el_plan_lee_solo_las_abiertas_y_todas_alimentan_el_grupo(base_casos: Any) -> None:
    coleccion = base_casos["alertas"]
    total = await coleccion.count_documents({})
    abiertas = await coleccion.count_documents({"estado": {"$in": ["nueva", "en_revision"]}})
    assert abiertas < total

    indice, devueltos, examinados, _, _ = await _explicar(base_casos, pipeline_casos(limite=50))

    assert indice == "idx_alertas_estado_puntaje_fecha"
    # Cada documento examinado entra al grupo: ninguno se lee para descartarlo.
    assert devueltos == examinados == abiertas < total


@pytest.mark.asyncio
async def prueba_el_plan_de_un_solo_caso_usa_el_indice_por_destino(base_casos: Any) -> None:
    indice, devueltos, examinados, llaves, _ = await _explicar(
        base_casos, pipeline_casos(cuenta=MULA)
    )
    assert indice == "idx_alertas_destino_estado"
    assert devueltos == examinados == 3
    assert llaves <= 4  # 3 + la llave de corte del rango; no recorre las demas alertas


def _sesion(agente: str = "AGT-001") -> Any:
    from app.modelos import RolAgente

    return Sesion(agente_id=agente, rol=RolAgente.AGENTE, expira=datetime(2099, 1, 1, tzinfo=UTC))


@pytest.mark.asyncio
async def prueba_el_lote_escribe_una_por_una_y_el_409_de_una_no_frena_a_las_demas(
    base_casos: Any,
) -> None:
    repo = RepoAlertas(base_casos)
    # ALR-102 la gana otro agente justo antes del lote.
    await base_casos["alertas"].update_one(
        {"_id": "ALR-102"}, {"$set": {"estado": "en_revision", "agente_id": "AGT-002"}}
    )

    resultado = await aplicar_lote(
        ["ALR-101", "ALR-102", "ALR-103", "ALR-104"],
        EstadoAlerta.EN_REVISION,
        "[lote] prueba",
        repo,
        _sesion(),
    )

    por_id = {r.alerta_id: r for r in resultado.resultados}
    assert (resultado.aplicadas, resultado.rechazadas) == (2, 2)
    assert por_id["ALR-101"].aplicada and por_id["ALR-103"].aplicada
    assert por_id["ALR-102"].estado_http == 409 and por_id["ALR-104"].estado_http == 409
    for alerta_id in ("ALR-101", "ALR-103"):
        guardada = await repo.obtener(alerta_id)
        assert guardada["estado"] == "en_revision" and guardada["agente_id"] == "AGT-001"
        assert [h["accion"] for h in guardada["historial"]] == ["nueva", "en_revision"]
        assert guardada["historial"][-1]["nota"] == "[lote] prueba"
    # La que perdio la carrera quedo como la dejo el otro agente.
    assert (await repo.obtener("ALR-102"))["agente_id"] == "AGT-002"
    # El caso ya solo tiene sus alertas abiertas, sin la confirmada.
    caso = await RepoCasosDestino(base_casos).obtener(MULA)
    assert caso is not None and caso.total_alertas == 3


@pytest.mark.asyncio
async def prueba_cerrar_el_lote_saca_las_alertas_del_caso(base_casos: Any) -> None:
    repo = RepoAlertas(base_casos)
    ids = ["ALR-101", "ALR-102", "ALR-103"]
    await aplicar_lote(ids, EstadoAlerta.EN_REVISION, "", repo, _sesion())
    resultado = await aplicar_lote(ids, EstadoAlerta.DESCARTADA, "falso positivo", repo, _sesion())
    assert resultado.aplicadas == 3
    assert await RepoCasosDestino(base_casos).obtener(MULA) is None


async def _tomar(repo: RepoAlertas, ids: list[str]) -> None:
    await aplicar_lote(ids, EstadoAlerta.EN_REVISION, "", repo, _sesion())


@pytest.mark.asyncio
async def prueba_escalar_un_lote_de_tres_abre_un_solo_caso_con_las_tres(base_casos: Any) -> None:
    repo = RepoAlertas(base_casos)
    ids = ["ALR-101", "ALR-102", "ALR-103"]
    await _tomar(repo, ids)

    resultado = await aplicar_lote(ids, EstadoAlerta.ESCALADA, "lote", repo, _sesion())

    assert resultado.aplicadas == 3
    assert {r.caso_id for r in resultado.resultados} == {"CAS-101"}
    casos = await base_casos["casos"].find({"alertas": {"$in": ids}}).to_list(length=None)
    assert [c["_id"] for c in casos] == ["CAS-101"]
    assert casos[0]["alertas"] == ids
    assert (casos[0]["cuenta_destino"], casos[0]["agente_id"]) == (MULA, "AGT-001")
    assert casos[0]["estado"] == "investigacion"
    for alerta_id in ids:
        guardada = await repo.obtener(alerta_id)
        assert guardada["estado"] == "escalada"
        assert [h["accion"] for h in guardada["historial"]] == ["nueva", "en_revision", "escalada"]


@pytest.mark.asyncio
async def prueba_escalar_un_lote_con_una_409_deja_un_caso_con_dos(base_casos: Any) -> None:
    repo = RepoAlertas(base_casos)
    ids = ["ALR-101", "ALR-102", "ALR-103"]
    await _tomar(repo, ids)
    # ALR-102 la cierra otro agente justo antes del lote: no esta en revision del agente.
    await base_casos["alertas"].update_one(
        {"_id": "ALR-102"}, {"$set": {"agente_id": "AGT-002"}}
    )

    resultado = await aplicar_lote(ids, EstadoAlerta.ESCALADA, "", repo, _sesion())

    por_id = {r.alerta_id: r for r in resultado.resultados}
    assert (resultado.aplicadas, resultado.rechazadas) == (2, 1)
    assert por_id["ALR-102"].estado_http == 409 and por_id["ALR-102"].caso_id is None
    casos = await base_casos["casos"].find({"alertas": {"$in": ids}}).to_list(length=None)
    assert [(c["_id"], c["alertas"]) for c in casos] == [("CAS-101", ["ALR-101", "ALR-103"])]
    assert (await repo.obtener("ALR-102"))["estado"] == "en_revision"


@pytest.mark.asyncio
async def prueba_la_primera_en_fallar_no_impide_que_otra_abra_el_caso(base_casos: Any) -> None:
    repo = RepoAlertas(base_casos)
    ids = ["ALR-101", "ALR-102", "ALR-103"]
    await _tomar(repo, ids)
    await base_casos["alertas"].update_one({"_id": "ALR-101"}, {"$set": {"agente_id": "AGT-002"}})

    resultado = await aplicar_lote(ids, EstadoAlerta.ESCALADA, "", repo, _sesion())

    assert resultado.aplicadas == 2
    casos = await base_casos["casos"].find({"alertas": {"$in": ids}}).to_list(length=None)
    assert [(c["_id"], c["alertas"]) for c in casos] == [("CAS-102", ["ALR-102", "ALR-103"])]


@pytest.mark.asyncio
async def prueba_si_fallan_todas_no_queda_un_caso_vacio(base_casos: Any) -> None:
    repo = RepoAlertas(base_casos)
    antes = await base_casos["casos"].count_documents({})
    # Siguen `nueva`: nueva -> escalada no esta en la tabla, ninguna pasa.
    resultado = await aplicar_lote(
        ["ALR-101", "ALR-102"], EstadoAlerta.ESCALADA, "", repo, _sesion()
    )
    assert resultado.aplicadas == 0
    assert await base_casos["casos"].count_documents({}) == antes


@pytest.mark.asyncio
async def prueba_si_la_carrera_ocurre_al_agregar_no_queda_en_el_caso(base_casos: Any) -> None:
    """La segunda alerta cambia entre leerla y escribirla: ni se mueve ni entra al caso."""
    repo = RepoAlertas(base_casos)
    ids = ["ALR-101", "ALR-102"]
    await _tomar(repo, ids)
    lote = CasoDeLote()
    ahora = datetime(2026, 9, 25, 5, 0, tzinfo=UTC)
    primera = planear_transicion(
        await repo.obtener("ALR-101"), destino=EstadoAlerta.ESCALADA, agente_id="AGT-001",
        nota="", ahora=ahora, lote=lote,
    )
    await repo.aplicar_transicion(primera)
    lote.caso_id = primera.caso["_id"]
    plan = planear_transicion(
        await repo.obtener("ALR-102"), destino=EstadoAlerta.ESCALADA, agente_id="AGT-001",
        nota="", ahora=ahora, lote=lote,
    )
    assert plan.caso is None and plan.agregar_a_caso == "CAS-101"
    await base_casos["alertas"].update_one({"_id": "ALR-102"}, {"$set": {"agente_id": "AGT-002"}})

    with pytest.raises(ConflictoAlerta):
        await repo.aplicar_transicion(plan)

    caso = await base_casos["casos"].find_one({"_id": "CAS-101"})
    assert caso["alertas"] == ["ALR-101"]
    assert (await repo.obtener("ALR-102"))["estado"] == "en_revision"


@pytest.mark.asyncio
async def prueba_la_escalacion_individual_sigue_siendo_un_caso_por_alerta(base_casos: Any) -> None:
    repo = RepoAlertas(base_casos)
    await _tomar(repo, ["ALR-101", "ALR-102"])
    for alerta_id in ("ALR-101", "ALR-102"):
        actual = await repo.obtener(alerta_id)
        plan = planear_transicion(
            actual, destino=EstadoAlerta.ESCALADA, agente_id="AGT-001", nota="",
            ahora=datetime(2026, 9, 25, 5, 0, tzinfo=UTC),
        )
        await repo.aplicar_transicion(plan)
    docs = await base_casos["casos"].find({"_id": {"$in": ["CAS-101", "CAS-102"]}}).to_list(length=None)
    assert [d["alertas"] for d in docs] == [["ALR-101"], ["ALR-102"]]
    assert all("cuenta_destino" not in d and "agente_id" not in d for d in docs)
