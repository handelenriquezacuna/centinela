"""H-14 · Resolver una alerta contra MongoDB de verdad.

Base desechable con los validadores reales de `alertas` y `casos` (copiados de
`antifraude`) y las alertas del demo. Lo que sostiene:

- el historial se AGREGA: la entrada de creacion sigue intacta despues de resolver;
- dos agentes que toman la misma alerta: el segundo recibe conflicto y no escribe;
- escalar crea el caso y mueve la alerta en la misma transaccion;
- si la transaccion falla (la alerta cambio en el medio, o el caso ya existe), no
  queda ni el caso ni el cambio en la alerta.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio

from app.modelos import Alerta, EstadoAlerta
from app.repos.alertas import ConflictoAlerta, RepoAlertas, planear_transicion
from app.repos.carga_demo import cargar_demo
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from pruebas.conftest import saltar_o_fallar
from pruebas.dobles.datos_demo import DATOS_DEMO

pytestmark = pytest.mark.mongo

BASE_DE_TRABAJO = "antifraude"
BASE_H14 = "antifraude_pruebas_h14"
AHORA = datetime(2026, 9, 25, 5, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def base_h14(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    validadores = await obtener_validadores(cliente[BASE_DE_TRABAJO])
    faltan = [c for c in ("alertas", "casos") if c not in validadores]
    if faltan:
        saltar_o_fallar(f"sin validador en antifraude para {faltan}; correr 01-colecciones.js")

    await cliente.drop_database(BASE_H14)
    base = cliente[BASE_H14]
    for nombre in ("alertas", "casos"):
        await recrear_con_validador(base, nombre, validadores[nombre])
    await cargar_demo(base, {"alertas": DATOS_DEMO["alertas"]})
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_H14)


async def _plan(repo: RepoAlertas, alerta_id: str, destino: EstadoAlerta, agente: str) -> Any:
    actual = await repo.obtener(alerta_id)
    return planear_transicion(actual, destino=destino, agente_id=agente, nota="", ahora=AHORA)


@pytest.mark.asyncio
async def prueba_tomar_agrega_al_historial_sin_reescribirlo(base_h14: Any) -> None:
    repo = RepoAlertas(base_h14)
    antes = await repo.obtener("ALR-002")

    despues = await repo.aplicar_transicion(
        await _plan(repo, "ALR-002", EstadoAlerta.EN_REVISION, "AGT-001")
    )

    assert despues["estado"] == "en_revision"
    assert despues["agente_id"] == "AGT-001"
    assert despues["historial"][:-1] == antes["historial"]
    assert despues["historial"][-1]["accion"] == "en_revision"
    Alerta.model_validate(despues)


@pytest.mark.asyncio
async def prueba_si_otro_agente_ya_la_tomo_es_conflicto(base_h14: Any) -> None:
    repo = RepoAlertas(base_h14)
    # Los dos leyeron ALR-002 en `nueva` al mismo tiempo.
    del_primero = await _plan(repo, "ALR-002", EstadoAlerta.EN_REVISION, "AGT-001")
    del_segundo = await _plan(repo, "ALR-002", EstadoAlerta.EN_REVISION, "AGT-002")

    await repo.aplicar_transicion(del_primero)
    with pytest.raises(ConflictoAlerta):
        await repo.aplicar_transicion(del_segundo)

    guardada = await repo.obtener("ALR-002")
    assert guardada["agente_id"] == "AGT-001"
    assert len(guardada["historial"]) == 2


@pytest.mark.asyncio
async def prueba_escalar_crea_el_caso_en_la_misma_transaccion(base_h14: Any) -> None:
    repo = RepoAlertas(base_h14)

    escalada = await repo.aplicar_transicion(
        await _plan(repo, "ALR-003", EstadoAlerta.ESCALADA, "AGT-002")
    )

    caso = await base_h14["casos"].find_one({"_id": "CAS-003"})
    assert escalada["estado"] == "escalada"
    assert caso["alertas"] == ["ALR-003"]
    assert caso["estado"] == "investigacion"


@pytest.mark.asyncio
async def prueba_si_la_alerta_cambio_no_queda_ni_caso_ni_cambio(base_h14: Any) -> None:
    """El caso se inserta PRIMERO dentro de la transaccion; despues la alerta ya no
    coincide. Si la transaccion no deshiciera, CAS-003 quedaria huerfano."""
    repo = RepoAlertas(base_h14)
    escalar = await _plan(repo, "ALR-003", EstadoAlerta.ESCALADA, "AGT-002")
    # Entre la lectura y la escritura, el mismo agente la confirma desde otra pestana.
    await repo.aplicar_transicion(
        await _plan(repo, "ALR-003", EstadoAlerta.CONFIRMADA, "AGT-002")
    )
    antes = await repo.obtener("ALR-003")

    with pytest.raises(ConflictoAlerta):
        await repo.aplicar_transicion(escalar)

    assert await base_h14["casos"].count_documents({}) == 0
    assert await repo.obtener("ALR-003") == antes


@pytest.mark.asyncio
async def prueba_si_el_caso_ya_existe_la_alerta_no_se_mueve(base_h14: Any) -> None:
    repo = RepoAlertas(base_h14)
    await base_h14["casos"].insert_one(
        {"_id": "CAS-003", "alertas": ["ALR-001"], "estado": "investigacion", "esquema_version": 1}
    )
    antes = await repo.obtener("ALR-003")

    with pytest.raises(ConflictoAlerta):
        await repo.aplicar_transicion(
            await _plan(repo, "ALR-003", EstadoAlerta.ESCALADA, "AGT-002")
        )

    assert await repo.obtener("ALR-003") == antes
    assert (await base_h14["casos"].find_one({"_id": "CAS-003"}))["alertas"] == ["ALR-001"]
