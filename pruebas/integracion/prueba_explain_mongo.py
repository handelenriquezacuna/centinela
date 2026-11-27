"""H-06 · `explain("executionStats")` de las consultas del panel contra Mongo de verdad.

Base propia (`antifraude_pruebas_explain`, T-34: ninguna prueba lee `antifraude`): se
borra, se le aplican los indices reales de `scripts/semilla/02-indices.js` con mongosh y
se llena con `ALERTAS_SINTETICAS` alertas deterministas (estado, severidad, puntaje y
fecha repartidos). Volumen fijo: las cifras no dependen de lo que haya en la base local.

Por cada consulta del panel (filtros de `construir_filtro` y orden `ORDEN_PANEL`, los
mismos que usa la API) se afirma:

1. el plan ganador hace IXSCAN con el indice que le corresponde por la regla igualdad,
   orden, rango (D-20 y H-06);
2. no hay etapa SORT: el orden del panel sale del indice, no de la memoria;
3. `totalDocsExamined` no supera 2 veces `nReturned`, salvo el rango de fechas, donde
   el rango va despues del orden y se recorren claves, no documentos de mas.

Para ver la tabla de planes: `uv run pytest -q -s pruebas/integracion/prueba_explain_mongo.py -m mongo`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio

from app.modelos import EstadoAlerta, Severidad
from app.nucleo.config import Ajustes
from app.nucleo.db import ciclo_cliente
from app.repos.alertas import (
    INDICE_PRIORITARIAS,
    ORDEN_PANEL,
    PRIORITARIA,
    _con,
    construir_filtro,
)
from pruebas.conftest import MENSAJE_SIN_MONGO, RUTA_RAIZ, saltar_o_fallar

pytestmark = pytest.mark.mongo

BASE_EXPLAIN = "antifraude_pruebas_explain"
ALERTAS_SINTETICAS = 3000
# Tamano de pagina del panel: la consulta que se explica usa el mismo limite.
LIMITE_PAGINA = 50
FACTOR_MAXIMO_DOCS = 2

# (filtro, indice que lo sirve segun scripts/semilla/02-indices.js, acota documentos)
CONSULTAS: list[Any] = [
    pytest.param(
        {"estado": EstadoAlerta.NUEVA},
        "idx_alertas_estado_puntaje_fecha",
        True,
        id="cola_nueva",
    ),
    pytest.param(
        {"estado": EstadoAlerta.EN_REVISION},
        "idx_alertas_estado_puntaje_fecha",
        True,
        id="cola_en_revision",
    ),
    pytest.param(
        {"estado": EstadoAlerta.NUEVA, "severidad": Severidad.CRITICA},
        "idx_alertas_estado_severidad_fecha",
        True,
        id="cola_nueva_severidad_critica",
    ),
    pytest.param(
        {"severidad": Severidad.CRITICA},
        "idx_alertas_severidad_puntaje_fecha",
        True,
        id="severidad_critica",
    ),
    pytest.param(
        {
            "desde": datetime(2026, 9, 1, tzinfo=UTC),
            "hasta": datetime(2026, 9, 15, tzinfo=UTC),
        },
        "idx_alertas_puntaje_fecha",
        False,
        id="rango_de_fechas",
    ),
    pytest.param({}, "idx_alertas_puntaje_fecha", True, id="sin_filtros"),
]


@dataclass(frozen=True)
class PlanResumido:
    indice: str | None
    hay_sort: bool
    n_returned: int
    docs_examined: int
    millis: int


def etapas_del_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Todas las etapas del plan ganador, la propia incluida."""
    etapas = [plan]
    hijas = [plan.get("inputStage"), *plan.get("inputStages", [])]
    for hija in hijas:
        if hija:
            etapas.extend(etapas_del_plan(hija))
    return etapas


def indice_del_plan(plan: dict[str, Any]) -> str | None:
    """Nombre del IXSCAN del plan ganador, o `None` si no hay ninguno (COLLSCAN)."""
    for etapa in etapas_del_plan(plan):
        if etapa.get("stage") == "IXSCAN":
            return etapa.get("indexName")
    return None


async def explicar(base: Any, filtro: dict[str, Any], hint: str | None = None) -> PlanResumido:
    """`explain("executionStats")` de la consulta de la cola, con el orden y el limite del panel."""
    respuesta = await base.command(
        {
            "explain": {
                "find": "alertas",
                "filter": filtro,
                "sort": dict(ORDEN_PANEL),
                "limit": LIMITE_PAGINA,
                **({"hint": hint} if hint else {}),
            },
            "verbosity": "executionStats",
        }
    )
    plan = respuesta["queryPlanner"]["winningPlan"]
    stats = respuesta["executionStats"]
    resumen = PlanResumido(
        indice=indice_del_plan(plan),
        hay_sort=any(etapa.get("stage") == "SORT" for etapa in etapas_del_plan(plan)),
        n_returned=stats["nReturned"],
        docs_examined=stats["totalDocsExamined"],
        millis=stats["executionTimeMillis"],
    )
    print(
        f"\n{filtro} | indice={resumen.indice} | sort={resumen.hay_sort} | nReturned={resumen.n_returned} "
        f"| totalDocsExamined={resumen.docs_examined} | ms={resumen.millis}"
    )
    return resumen


_JS_INDICES = """
const destino = db.getMongo().getDB("%(base)s");
destino.dropDatabase();
db.getSiblingDB = function () { return destino; };
// 01 reasigna `db` a `destino`; 02 vuelve a llamar getSiblingDB sobre el: tambien se
// redirige, o los indices irian a `antifraude`.
destino.getSiblingDB = function () { return destino; };
print = function () {};
load("%(semilla)s/01-colecciones.js");
load("%(semilla)s/02-indices.js");
// Base desechable: las alertas sinteticas solo llevan los campos que el plan mira.
destino.runCommand({ collMod: "alertas", validator: {}, validationLevel: "off" });
"""

ESTADOS = [e.value for e in EstadoAlerta]
# (severidad, puntaje) dentro de los cortes de POL-001 v2
SEVERIDADES = [
    ("MEDIA", 35),
    ("MEDIA", 50),
    ("ALTA", 60),
    ("CRITICA", 75),
    ("CRITICA", 100),
]


def _alertas_sinteticas() -> list[dict[str, Any]]:
    """Reparto determinista: sin azar, el plan y las cifras se repiten en cada corrida."""
    inicio = datetime(2026, 8, 1, tzinfo=UTC)
    documentos = []
    for numero in range(1, ALERTAS_SINTETICAS + 1):
        severidad, puntaje = SEVERIDADES[numero % len(SEVERIDADES)]
        documento = {
                "_id": f"ALR-{numero:05d}",
                "transaccion_id": f"TXN-{numero:05d}",
                "estado": ESTADOS[(numero // 7) % len(ESTADOS)],
                "severidad": severidad,
                "puntaje": puntaje,
                "fecha_creacion": inicio + timedelta(minutes=37 * numero),
                "agente_id": f"AGT-{1 + numero % 4:03d}",
            }
        # T-82: unas pocas alertas con la respuesta "No fui yo" del cliente (4 de cada mil).
        if numero % 250 == 0:
            documento["respuesta_cliente"] = {"valor": "no_fui_yo", "fecha": inicio}
        documentos.append(documento)
    return documentos


async def _cargar_indices(uri: str) -> None:
    import asyncio
    import shutil
    import subprocess

    mongosh = shutil.which("mongosh")
    if mongosh is None:
        saltar_o_fallar(
            "mongosh no esta en el PATH; hace falta para aplicar 02-indices.js"
        )
    js = _JS_INDICES % {
        "base": BASE_EXPLAIN,
        "semilla": str(RUTA_RAIZ / "scripts" / "semilla"),
    }
    resultado = await asyncio.to_thread(
        subprocess.run,
        [mongosh, uri, "--quiet", "--eval", js],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if resultado.returncode != 0:
        saltar_o_fallar(
            f"no se pudo aplicar 02-indices.js: {resultado.stdout}{resultado.stderr}"
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def base_generada() -> AsyncIterator[Any]:
    """Base propia con los indices reales y alertas sinteticas."""
    ajustes = Ajustes()
    async with ciclo_cliente(ajustes) as cliente:
        try:
            await cliente.admin.command("ping")
        except Exception:  # noqa: BLE001 - cualquier falla de conexion es "no responde"
            saltar_o_fallar(MENSAJE_SIN_MONGO)
        await _cargar_indices(ajustes.mongo.uri)
        base = cliente[BASE_EXPLAIN]
        await base.alertas.insert_many(_alertas_sinteticas())
        yield base
        await cliente.drop_database(BASE_EXPLAIN)


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(("kwargs", "indice", "acota_docs"), CONSULTAS)
async def prueba_plan_usa_su_indice_sin_sort_en_memoria(
    base_generada: Any, kwargs: dict[str, Any], indice: str, acota_docs: bool
) -> None:
    plan = await explicar(base_generada, construir_filtro(**kwargs))
    assert plan.indice == indice, (
        f"el plan ganador usa {plan.indice}, se esperaba IXSCAN con {indice}"
    )
    assert not plan.hay_sort, "el orden del panel se hace en memoria (etapa SORT)"
    if acota_docs:
        maximo = FACTOR_MAXIMO_DOCS * plan.n_returned
        assert plan.docs_examined <= maximo, (
            f"totalDocsExamined={plan.docs_examined} supera {FACTOR_MAXIMO_DOCS} x "
            f"nReturned={plan.n_returned}"
        )
    assert plan.n_returned > 0, (
        "la consulta no devolvio nada: el volumen sintetico no la cubre"
    )


# ---------------------------------------------------------------------------
# T-82 · la cola con las alertas "No fui yo" arriba: dos tramos, cada uno con su indice
# ---------------------------------------------------------------------------

PRIORITARIAS_ESPERADAS = [n for n in range(1, ALERTAS_SINTETICAS + 1) if n % 250 == 0]


@pytest.mark.asyncio(loop_scope="module")
async def prueba_el_tramo_de_prioritarias_lee_su_indice_parcial_sin_sort(base_generada: Any) -> None:
    plan = await explicar(base_generada, _con({}, PRIORITARIA), hint=INDICE_PRIORITARIAS)

    assert plan.indice == "idx_alertas_no_fui_yo"
    assert not plan.hay_sort
    # Solo las del indice parcial, de las abiertas: nada de recorrer la coleccion.
    assert plan.docs_examined <= len(PRIORITARIAS_ESPERADAS)


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(
    ("filtro", "indice"),
    [
        ({}, "idx_alertas_puntaje_fecha"),
        ({"estado": EstadoAlerta.NUEVA.value}, "idx_alertas_estado_puntaje_fecha"),
        ({"severidad": Severidad.CRITICA.value}, "idx_alertas_severidad_puntaje_fecha"),
    ],
    ids=["resto_sin_filtros", "resto_nueva", "resto_critica"],
)
async def prueba_el_tramo_del_resto_sigue_sin_sort_y_cuesta_solo_las_prioritarias(
    base_generada: Any, filtro: dict[str, Any], indice: str
) -> None:
    plan = await explicar(base_generada, _con(filtro, {"$nor": [PRIORITARIA]}))

    assert plan.indice == indice
    assert not plan.hay_sort, "el $nor de las prioritarias no debe forzar un SORT en memoria"
    # El unico costo: leer (y descartar) las prioritarias que caen dentro de lo recorrido.
    assert plan.docs_examined <= plan.n_returned + len(PRIORITARIAS_ESPERADAS)
