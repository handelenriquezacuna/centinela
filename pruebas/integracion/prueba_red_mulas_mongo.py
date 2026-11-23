"""H-23 contra MongoDB de verdad: la red de mulas de una cadena generada.

El generador (H-05) arma cadenas victima -> mula 1 -> ... -> mula N -> cuenta externa,
cada eslabon una transaccion etiquetada con el mismo episodio. La prueba recorre la red
desde la victima y comprueba que aparecen TODOS los eslabones, en orden de salto y con
sus montos, que la salida externa queda como nodo externo y que las receptoras se
proponen para la lista de riesgo (sin escribir en ella).

No hace falta D1: la red se lee de `transacciones` (con `sinpe_origen`, que el
generador escribe siempre) y `cuentas`. Base propia,
`antifraude_pruebas_red_mulas`, con los validadores reales copiados de `antifraude` y los
indices de `02-indices.js` que usa la consulta, con el mismo nombre.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from itertools import pairwise
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.generador.ejecucion import Conjunto, ejecutar, generar_conjunto
from app.generador.fraude import CADENA
from app.main import crear_aplicacion
from app.nucleo.config import AjustesGenerador, obtener_ajustes
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.generador import RepoGenerador
from app.repos.red_mulas import SALTOS_MAXIMOS, RepoRedMulas
from pruebas.conftest import BASE_SEMILLA, saltar_o_fallar
from pruebas.sesiones import encabezados_de

pytestmark = pytest.mark.mongo

BASE_DE_TRABAJO = BASE_SEMILLA  # semilla propia de las pruebas, no `antifraude`
BASE_RED = "antifraude_pruebas_red_mulas"
COLECCIONES = (
    "clientes",
    "cuentas",
    "perfiles_comportamiento",
    "transacciones",
    "listas_riesgo",
)
GENERADOR = AjustesGenerador(clientes=200, transacciones=5000)
INDICE_GRAFO = "idx_transacciones_sinpe_origen_fecha"
INDICE_ORIGEN = "idx_transacciones_cuenta_origen_fecha"
INDICE_PUENTE = "idx_cuentas_sinpe"


@pytest_asyncio.fixture
async def base_red(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    validadores = await obtener_validadores(cliente[BASE_DE_TRABAJO])
    faltan = [nombre for nombre in COLECCIONES if nombre not in validadores]
    if faltan:
        saltar_o_fallar(f"sin validador en {BASE_DE_TRABAJO} para {faltan}")

    await cliente.drop_database(BASE_RED)
    base = cliente[BASE_RED]
    for nombre in COLECCIONES:
        await recrear_con_validador(base, nombre, validadores[nombre])
    await base["transacciones"].create_index(
        [("sinpe_origen", 1), ("fecha", 1)], name=INDICE_GRAFO
    )
    # El de la busqueda forense, presente como en la base real: la prueba de indices
    # comprueba que el recorrido NO va por este.
    await base["transacciones"].create_index(
        [("cuenta_origen", 1), ("fecha", -1)], name=INDICE_ORIGEN
    )
    await base["cuentas"].create_index("cuenta_sinpe", unique=True, name=INDICE_PUENTE)
    await base["listas_riesgo"].create_index(
        "cuenta", unique=True, name="idx_listas_riesgo_cuenta"
    )
    await ejecutar(RepoGenerador(base), GENERADOR, limpiar=False)
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_RED)


@pytest.fixture(scope="module")
def conjunto() -> Conjunto:
    return generar_conjunto(GENERADOR)


def _cadenas(conjunto: Conjunto) -> dict[str, list[dict]]:
    """Eslabones de cada episodio de cadena, en orden cronologico."""
    episodios: dict[str, list[dict]] = {}
    for txn in conjunto.transacciones:
        if txn["simulacion"].get("patron") == CADENA:
            episodios.setdefault(txn["simulacion"]["episodio"], []).append(txn)
    return episodios


def _etapas(explicacion: Any) -> list[str]:
    """Nombres de las etapas de la agregacion explicada, en orden."""
    return [next(iter(etapa)) for etapa in explicacion.get("stages", [])]


def _indices_de_cursor(explicacion: Any) -> Iterator[str]:
    """Nombres de indice en cualquier rincon de un `explain` (cambia entre motores)."""
    if isinstance(explicacion, dict):
        for clave, valor in explicacion.items():
            if clave == "indexName" and isinstance(valor, str):
                yield valor
            else:
                yield from _indices_de_cursor(valor)
    elif isinstance(explicacion, list):
        for elemento in explicacion:
            yield from _indices_de_cursor(elemento)


@pytest.mark.asyncio
async def prueba_la_red_desde_la_victima_trae_todos_los_eslabones_en_orden(
    base_red: Any, conjunto: Conjunto
) -> None:
    cadenas = _cadenas(conjunto)
    assert cadenas, "el generador siempre inyecta al menos una cadena"
    listadas = {documento["cuenta"] for documento in conjunto.listas_riesgo}
    sinpe = {cuenta["_id"]: cuenta["cuenta_sinpe"] for cuenta in conjunto.cuentas}
    repo = RepoRedMulas(base_red)

    for episodio, eslabones in cadenas.items():
        primero = eslabones[0]
        red = await repo.recorrer(primero["cuenta_origen"], desde=primero["fecha"])

        por_id = {arista.transaccion_id: arista for arista in red.aristas}
        faltan = [txn["_id"] for txn in eslabones if txn["_id"] not in por_id]
        assert not faltan, f"{episodio}: faltan eslabones {faltan}"
        recorridas = [por_id[txn["_id"]] for txn in eslabones]
        assert [arista.salto for arista in recorridas] == list(range(1, len(eslabones) + 1))
        assert [arista.monto_crc for arista in recorridas] == [t["monto_crc"] for t in eslabones]
        # Cada eslabon sale de donde llego el anterior, y despues.
        for anterior, siguiente in pairwise(recorridas):
            assert siguiente.origen == anterior.destino
            assert siguiente.fecha > anterior.fecha
            assert siguiente.minutos_desde_anterior == round(
                (siguiente.fecha - anterior.fecha).total_seconds() / 60, 1
            )
        assert recorridas[0].origen == sinpe[primero["cuenta_origen"]]
        assert red.saltos_recorridos <= SALTOS_MAXIMOS

        nodos = {nodo.cuenta: nodo for nodo in red.nodos}
        salida = nodos[eslabones[-1]["cuenta_destino"]]
        assert salida.externa and salida.cuenta_id is None, "la cadena sale del banco"
        receptoras = [txn["cuenta_destino"] for txn in eslabones]
        for cuenta in receptoras:
            assert nodos[cuenta].en_lista_riesgo == (cuenta in listadas)
            assert (cuenta in red.propuestas_lista_riesgo) == (cuenta not in listadas)
        assert red.cuenta not in red.propuestas_lista_riesgo, "la victima no se propone"

    # Proponer no es escribir.
    assert await base_red["listas_riesgo"].count_documents({}) == len(conjunto.listas_riesgo)


@pytest.mark.asyncio
async def prueba_desde_la_primera_mula_el_salto_1_es_su_salida(
    base_red: Any, conjunto: Conjunto
) -> None:
    """Como en UC-04: se parte del destino de la alerta (un numero SINPE)."""
    eslabones = next(iter(_cadenas(conjunto).values()))
    primero = eslabones[0]

    red = await RepoRedMulas(base_red).recorrer(
        primero["cuenta_destino"], desde=primero["fecha"]
    )

    por_id = {arista.transaccion_id: arista for arista in red.aristas}
    assert [por_id[txn["_id"]].salto for txn in eslabones[1:]] == list(
        range(1, len(eslabones))
    )
    assert primero["_id"] not in por_id, "lo que entro a la cuenta no es una salida"


@pytest.mark.asyncio
async def prueba_el_recorrido_es_un_graphlookup_por_el_indice_nuevo(
    base_red: Any, conjunto: Conjunto
) -> None:
    """El plan: `$match` en `cuentas` por `idx_cuentas_sinpe`, y un `$graphLookup` de
    6 niveles cuyas busquedas van por `idx_transacciones_sinpe_origen_fecha`.

    El `explain` de un `$graphLookup` no nombra el indice de sus busquedas internas;
    el contador de `$indexStats` si. Sube el del indice nuevo y no el de
    `cuenta_origen`: el recorrido no volvio a ser un salto por agregacion.
    """
    primero = next(iter(_cadenas(conjunto).values()))[0]
    repo = RepoRedMulas(base_red)

    explicacion = await repo.explicar(primero["cuenta_origen"], desde=primero["fecha"])
    etapas = _etapas(explicacion)
    assert "$graphLookup" in etapas, etapas
    grafo = next(e["$graphLookup"] for e in explicacion["stages"] if "$graphLookup" in e)
    assert (grafo["connectFromField"], grafo["connectToField"]) == (
        "cuenta_destino",
        "sinpe_origen",
    )
    assert grafo["maxDepth"] == SALTOS_MAXIMOS - 1
    assert INDICE_PUENTE in set(_indices_de_cursor(explicacion))

    antes = await repo.usos_de_indices()
    red = await repo.recorrer(primero["cuenta_origen"], desde=primero["fecha"])
    despues = await repo.usos_de_indices()

    assert red.aristas
    usos = despues[INDICE_GRAFO] - antes[INDICE_GRAFO]
    print(f"\n$graphLookup: {usos} busquedas por {INDICE_GRAFO}, {red.saltos_recorridos} saltos")
    assert usos >= red.saltos_recorridos, (antes, despues)
    assert despues[INDICE_ORIGEN] == antes[INDICE_ORIGEN]


@pytest.mark.asyncio
async def prueba_la_api_devuelve_el_grafo(base_red: Any, conjunto: Conjunto) -> None:
    eslabones = next(iter(_cadenas(conjunto).values()))
    primero = eslabones[0]

    os.environ["CENTINELA_MONGO__BASE"] = BASE_RED
    obtener_ajustes.cache_clear()
    try:
        aplicacion = crear_aplicacion()
        async with LifespanManager(aplicacion) as gestionada, AsyncClient(
            headers=encabezados_de(),
            transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
        ) as http:
            respuesta = await http.get(
                f"/api/v1/red-mulas/{primero['cuenta_origen']}",
                params={"desde": primero["fecha"].isoformat()},
            )
            inexistente = await http.get("/api/v1/red-mulas/CTA-9999")
            mal_formada = await http.get("/api/v1/red-mulas/no-es-cuenta")
            demasiados = await http.get(
                f"/api/v1/red-mulas/{primero['cuenta_origen']}", params={"saltos": 7}
            )
    finally:
        os.environ.pop("CENTINELA_MONGO__BASE", None)
        obtener_ajustes.cache_clear()

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    ids = [arista["transaccion_id"] for arista in cuerpo["aristas"]]
    assert all(txn["_id"] in ids for txn in eslabones)
    assert {"nodos", "aristas", "propuestas_lista_riesgo"} <= set(cuerpo)
    assert inexistente.status_code == 404
    assert inexistente.json()["error"]["codigo"] == "no_encontrado"
    assert mal_formada.status_code == 422
    assert demasiados.status_code == 422
