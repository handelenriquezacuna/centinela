"""H-24 y datos de demo contra MongoDB de verdad.

Base `antifraude_pruebas_red_mulas` (la lista de 04-usuarios.js no cambia): se le aplica la semilla real (01, 02, 03 y 05 de
`scripts/semilla/`, con validadores e indices reales) y se carga el conjunto de demo con
`cargar_demo`. Asi se prueba tambien que cada documento del demo pasa su `$jsonSchema`.

Dos cosas:

1. La red de mulas del caso documentado (C-52): desde 6033-9001, con `desde` la fecha de
   TXN-001, dibuja 3 saltos de 276.000, 185.000 y 90.000.
2. Las tres vistas guardadas: cada una devuelve lo esperado sobre el demo, 05 es
   idempotente y el plan de la vista usa un indice de `alertas`.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.nucleo.config import Ajustes
from app.repos.carga_demo import cargar_demo
from app.repos.red_mulas import RepoRedMulas
from pruebas.conftest import RUTA_RAIZ, saltar_o_fallar
from pruebas.dobles.datos_demo import DATOS_DEMO, TRANSACCIONES

pytestmark = pytest.mark.mongo

# Base ya listada en 04-usuarios.js (BASES_PRUEBAS): la red de mulas del demo es la misma
# consulta que prueba_red_mulas_mongo.py, que se rehace en cada prueba. 04 no cambia.
BASE_DEMO = "antifraude_pruebas_red_mulas"
VISTAS = ("vista_alertas_abiertas", "vista_cuentas_en_riesgo", "vista_resumen_por_regla")

# Misma redireccion que `pruebas/conftest.py`: los scripts de semilla escriben a la base
# que dice `getSiblingDB`, y aqui se les dice que es la base de esta prueba.
_JS_SEMILLA = """
const destino = db.getMongo().getDB("%(base)s");
destino.dropDatabase();
db.getSiblingDB = function () { return destino; };
destino.getSiblingDB = function () { return destino; };
print = function () {};
load("%(semilla)s/01-colecciones.js");
load("%(semilla)s/02-indices.js");
var BASE_CENTINELA = "%(base)s";
load("%(semilla)s/03-catalogos.js");
load("%(semilla)s/05-vistas.js");
"""


def _sembrar(uri: str) -> None:
    mongosh = shutil.which("mongosh")
    if mongosh is None:
        saltar_o_fallar("mongosh no esta en el PATH; hace falta para sembrar la base de pruebas")
    js = _JS_SEMILLA % {"base": BASE_DEMO, "semilla": str(RUTA_RAIZ / "scripts" / "semilla")}
    resultado = subprocess.run(
        [mongosh, uri, "--quiet", "--eval", js],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if resultado.returncode != 0:
        saltar_o_fallar(f"no se pudo sembrar {BASE_DEMO}: {resultado.stdout}{resultado.stderr}")


@pytest_asyncio.fixture
async def base_demo(base_mongo: Any, ajustes: Ajustes) -> AsyncIterator[Any]:
    await asyncio.to_thread(_sembrar, ajustes.mongo.uri)
    base = base_mongo.client[BASE_DEMO]
    await cargar_demo(base, DATOS_DEMO)
    try:
        yield base
    finally:
        await base_mongo.client.drop_database(BASE_DEMO)


def _indices(explicacion: Any) -> set[str]:
    """Nombres de indice en cualquier rincon de un `explain`."""
    encontrados: set[str] = set()
    if isinstance(explicacion, dict):
        for clave, valor in explicacion.items():
            if clave == "indexName" and isinstance(valor, str):
                encontrados.add(valor)
            else:
                encontrados |= _indices(valor)
    elif isinstance(explicacion, list):
        for elemento in explicacion:
            encontrados |= _indices(elemento)
    return encontrados


@pytest.mark.asyncio
async def prueba_la_red_del_caso_dibuja_tres_saltos_con_sus_montos(base_demo: Any) -> None:
    txn_001 = next(t for t in TRANSACCIONES if t.transaccion_id == "TXN-001")

    red = await RepoRedMulas(base_demo).recorrer("6033-9001", desde=txn_001.fecha)

    assert red.saltos_recorridos == 3
    assert [a.salto for a in red.aristas] == [1, 2, 3]
    assert [a.monto_crc for a in red.aristas] == [276_000, 185_000, 90_000]
    assert [(a.origen, a.destino) for a in red.aristas] == [
        ("6033-9001", "7104-2288"),
        ("7104-2288", "8455-1177"),
        ("8455-1177", "6001-3344"),
    ]
    assert [a.minutos_desde_anterior for a in red.aristas] == [4.0, 9.0, 11.0]
    assert not any(nodo.externa for nodo in red.nodos), "las cuatro cuentas existen en cuentas"
    # D-55: solo el primer salto esta reportado; las otras tres son propuestas de la red.
    assert {n.cuenta: n.en_lista_riesgo for n in red.nodos} == {
        "6033-9001": True, "7104-2288": False, "8455-1177": False, "6001-3344": False,
    }
    assert red.propuestas_lista_riesgo == ["7104-2288", "8455-1177", "6001-3344"]


@pytest.mark.asyncio
async def prueba_alertas_abiertas_trae_solo_nueva_y_en_revision_con_cliente_y_monto(
    base_demo: Any,
) -> None:
    filas = await base_demo["vista_alertas_abiertas"].find({}).to_list()

    assert [f["_id"] for f in filas] == ["ALR-003", "ALR-002"], "mas grave primero"
    assert {f["estado"] for f in filas} == {"nueva", "en_revision"}
    assert [(f["cliente_nombre"], int(f["monto_crc"])) for f in filas] == [
        ("Carlos Mora Rojas", 12_750),
        ("Ana Castro Jimenez", 48_300),
    ]


@pytest.mark.asyncio
async def prueba_cuentas_en_riesgo_cuenta_alertas_y_marca_las_de_la_lista(
    base_demo: Any,
) -> None:
    filas = await base_demo["vista_cuentas_en_riesgo"].find({}).to_list()
    por_cuenta = {f["cuenta"]: f for f in filas}

    assert set(por_cuenta) == {"6033-9001", "3390-6612", "6210-3387"}
    assert all(f["cantidad_alertas"] == 1 for f in filas)
    assert por_cuenta["6033-9001"]["en_lista_riesgo"] is True
    assert int(por_cuenta["6033-9001"]["monto_total_crc"]) == 280_000
    assert por_cuenta["3390-6612"]["en_lista_riesgo"] is False


@pytest.mark.asyncio
async def prueba_resumen_por_regla_cuenta_las_alertas_de_cada_regla(base_demo: Any) -> None:
    filas = await base_demo["vista_resumen_por_regla"].find({}).to_list()
    por_regla = {f["_id"]: f for f in filas}

    assert [f["_id"] for f in filas] == ["REG-003", "REG-002", "REG-001"]
    assert {r: f["cantidad_alertas"] for r, f in por_regla.items()} == {
        "REG-001": 1,
        "REG-002": 2,
        "REG-003": 3,
    }
    assert por_regla["REG-001"]["codigo"] == "R-01"
    assert por_regla["REG-001"]["descripcion"] == "Monto muy superior al promedio del cliente"
    assert por_regla["REG-001"]["puntaje_promedio"] == 75


@pytest.mark.asyncio
async def prueba_la_vista_de_alertas_abiertas_usa_un_indice_de_alertas(base_demo: Any) -> None:
    explicacion = await base_demo.command(
        {
            "explain": {"aggregate": "vista_alertas_abiertas", "pipeline": [], "cursor": {}},
            "verbosity": "queryPlanner",
        }
    )

    indices = _indices(explicacion)
    assert indices, explicacion
    assert all(nombre.startswith("idx_alertas_") for nombre in indices), indices


@pytest.mark.asyncio
async def prueba_las_vistas_son_de_solo_lectura_y_la_siembra_es_idempotente(
    base_demo: Any, ajustes: Ajustes
) -> None:
    info = await base_demo.list_collections(filter={"type": "view"})
    assert sorted([v["name"] async for v in info]) == sorted(VISTAS)

    await asyncio.to_thread(_sembrar, ajustes.mongo.uri)  # segunda corrida, base nueva
    info = await base_demo.list_collections(filter={"type": "view"})
    assert len([v async for v in info]) == len(VISTAS)

    with pytest.raises(Exception, match="(?i)view"):
        await base_demo["vista_alertas_abiertas"].insert_one({"_id": "X"})
