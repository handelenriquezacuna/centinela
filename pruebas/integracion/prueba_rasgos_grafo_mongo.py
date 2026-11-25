"""T-63 contra MongoDB de verdad: rasgos de grafo con `$merge`, R-11 y el caso de la demo.

Base propia por prueba, que se tira al terminar (T-34: ninguna prueba lee `antifraude`).
Los validadores salen de `BASE_SEMILLA`, que carga el `01-colecciones.js` de la rama: si el
pipeline escribiera un rasgo que el validador de `deteccion_estado` rechaza (dinero que no
sea long, un campo de mas), `$merge` falla y la prueba con el. Los indices llevan el
nombre de `02-indices.js`.

Lo que se comprueba:

1. el pipeline calcula lo mismo que la definicion pura (`app/deteccion/rasgos_grafo.py`)
   para TODAS las cuentas de un conjunto generado, con dos ventanas;
2. el caso de la demo: 6033-9001 recibe 280.000 y reenvia 276.000, 98,6 %;
3. `$merge` es idempotente y borra lo de corridas anteriores;
4. D1, de punta a punta: la transaccion siguiente hacia 6033-9001 levanta alerta con
   R-11 y su explicacion;
5. el plan: `$match` por `idx_transacciones_fecha` y las busquedas del `$graphLookup` por
   `idx_transacciones_sinpe_origen_fecha`.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from pymongo.errors import WriteError

from app.deteccion import evaluar
from app.deteccion.d1 import NOMBRE_D1, d1
from app.deteccion.evaluador import ContextoEvaluacion
from app.deteccion.rasgos_grafo import (
    CLASE_CORRIDA,
    CLASE_RASGO,
    NOMBRE_RASGOS,
    calcular_rasgo,
    id_rasgo,
    indexar,
)
from app.generador.ejecucion import ejecutar
from app.modelos import Regla, Transaccion
from app.nucleo.config import AjustesDeteccion, AjustesGenerador, AjustesRasgosGrafo
from app.repos.carga_demo import a_entero_64, cargar_demo
from app.repos.deteccion import DatosOrigen, RepoDeteccion
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.generador import RepoGenerador
from app.repos.rasgos_grafo import RepoRasgosGrafo
from app.supervision import Supervisor
from pruebas.conftest import BASE_SEMILLA, saltar_o_fallar
from pruebas.dobles.datos_demo import DATOS_DEMO, TRANSACCIONES

pytestmark = pytest.mark.mongo

BASE_RASGOS = "antifraude_pruebas_rasgos"
VALIDADORES = (
    "clientes",
    "cuentas",
    "perfiles_comportamiento",
    "listas_riesgo",
    "transacciones",
    "alertas",
    "deteccion_estado",
)
CATALOGO = ("reglas_deteccion", "politica_deteccion")
DE_IDENTIDAD = ("clientes", "cuentas", "perfiles_comportamiento", "listas_riesgo")
INDICES = {
    "idx_transacciones_fecha": [("fecha", 1)],
    "idx_transacciones_sinpe_origen_fecha": [("sinpe_origen", 1), ("fecha", 1)],
    "idx_transacciones_cuenta_destino": [("cuenta_destino", 1)],
}
MULA = "6033-9001"
# 24-sep a las 24:00 en Costa Rica: la cadena de la demo (22:47 a 23:11) queda adentro.
CORTE_DEMO = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)
VIGENCIA = AjustesRasgosGrafo(horizonte_horas=24, vigencia_segundos=60)
AJUSTES_D1 = AjustesDeteccion(
    espera_cursor_ms=100, reintentos=1, espera_inicial_segundos=0.05, espera_maxima_segundos=0.2
)
LIMITE_SEGUNDOS = 20


async def _base(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    trabajo = cliente[BASE_SEMILLA]
    validadores = await obtener_validadores(trabajo)
    faltan = [nombre for nombre in VALIDADORES if nombre not in validadores]
    if faltan:
        saltar_o_fallar(f"sin validador en {BASE_SEMILLA} para {faltan}")
    await cliente.drop_database(BASE_RASGOS)
    base = cliente[BASE_RASGOS]
    for nombre in VALIDADORES:
        await recrear_con_validador(base, nombre, validadores[nombre])
    for nombre, clave in INDICES.items():
        await base["transacciones"].create_index(clave, name=nombre)
    await base["alertas"].create_index(
        "transaccion_id", unique=True, name="idx_alertas_transaccion_id"
    )
    for nombre in CATALOGO:
        documentos = await trabajo[nombre].find({}).to_list(length=None)
        if not documentos:
            saltar_o_fallar(f"{BASE_SEMILLA}.{nombre} vacia; correr 03-catalogos.js")
        await base[nombre].insert_many(documentos)
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_RASGOS)


@pytest_asyncio.fixture
async def base_demo(base_mongo: Any) -> AsyncIterator[Any]:
    """Identidad y catalogo del demo, mas TXN-001..007 (la cadena de 6033-9001)."""
    async for base in _base(base_mongo):
        await cargar_demo(base, {nombre: DATOS_DEMO[nombre] for nombre in DE_IDENTIDAD})
        await base["transacciones"].insert_many(
            [a_entero_64(t.a_documento()) for t in TRANSACCIONES]
        )
        yield base


@pytest_asyncio.fixture
async def base_generada(base_mongo: Any) -> AsyncIterator[Any]:
    """5.000 transacciones del generador, con sus cadenas de mulas."""
    async for base in _base(base_mongo):
        await ejecutar(
            RepoGenerador(base), AjustesGenerador(clientes=200, transacciones=5000), limpiar=False
        )
        yield base


def _repo(base: Any) -> RepoRasgosGrafo:
    return RepoRasgosGrafo(base, VIGENCIA)


async def _recalcular_demo(base: Any, *, ventana: int = 15) -> int:
    return await _repo(base).recalcular(
        CORTE_DEMO, ventana_minutos=ventana, horizonte=timedelta(hours=24)
    )


async def _rasgos(base: Any) -> dict[str, dict[str, Any]]:
    docs = await base["deteccion_estado"].find({"clase": CLASE_RASGO}).to_list(length=None)
    return {doc["cuenta"]: doc for doc in docs}


# ---------------------------------------------------------------------------
# El pipeline es la definicion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("ventana", [15, 60])
async def prueba_el_pipeline_calcula_lo_mismo_que_la_definicion_pura(
    base_generada: Any, ventana: int
) -> None:
    transacciones = [
        Transaccion.model_validate(doc)
        async for doc in base_generada["transacciones"].find({})
    ]
    corte = max(t.fecha for t in transacciones) + timedelta(seconds=1)
    horizonte = timedelta(days=60)
    desde = corte - horizonte
    corte = corte.replace(microsecond=corte.microsecond // 1000 * 1000)

    cuentas = await _repo(base_generada).recalcular(
        corte, ventana_minutos=ventana, horizonte=horizonte
    )

    por_destino, por_origen = indexar(transacciones, desde, corte)
    guardados = await _rasgos(base_generada)
    assert cuentas == len(guardados) == len(por_destino)
    for cuenta in por_destino:
        esperado = calcular_rasgo(
            cuenta, por_destino, por_origen,
            desde=desde, corte=corte, ventana_minutos=ventana, vigencia_segundos=60,
        )  # fmt: skip
        doc = guardados[cuenta]
        obtenido = (
            doc["fan_in"], doc["fan_out"], int(doc["monto_recibido_crc"]),
            int(doc["monto_reenviado_crc"]), doc["proporcion_pb"],
            doc["minutos_reenvio"], doc["saltos"],
        )  # fmt: skip
        assert obtenido == (
            esperado.fan_in, esperado.fan_out, esperado.monto_recibido_crc,
            esperado.monto_reenviado_crc, esperado.proporcion_pb,
            esperado.minutos_reenvio, esperado.saltos,
        ), cuenta  # fmt: skip

    # La comparacion no es trivial: el conjunto tiene reenvios y cadenas de verdad.
    assert any(d["proporcion_pb"] > 0 for d in guardados.values())
    assert any(d["saltos"] >= 3 for d in guardados.values())
    # `$merge` paso por el validador de deteccion_estado: dinero long, enteros int.
    assert await base_generada["deteccion_estado"].count_documents(
        {"clase": CLASE_RASGO, "monto_recibido_crc": {"$not": {"$type": "long"}}}
    ) == 0


# ---------------------------------------------------------------------------
# El caso de la demo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_mula_de_la_demo_reenvia_el_986_por_ciento(base_demo: Any) -> None:
    await _recalcular_demo(base_demo)

    doc = (await _rasgos(base_demo))[MULA]
    assert doc["_id"] == id_rasgo(MULA) == "d3_rasgos_grafo:6033-9001"
    assert (int(doc["monto_recibido_crc"]), int(doc["monto_reenviado_crc"])) == (280_000, 276_000)
    assert doc["proporcion_pb"] == 9857  # 98,6 %
    assert (doc["minutos_reenvio"], doc["saltos"], doc["fan_in"], doc["fan_out"]) == (4, 3, 1, 1)
    assert doc["ventana_minutos"] == 15
    corrida = await base_demo["deteccion_estado"].find_one({"_id": NOMBRE_RASGOS})
    assert corrida["clase"] == CLASE_CORRIDA
    assert corrida["cuentas"] == 7  # los 7 destinos distintos de TXN-001..007
    # Los eslabones intermedios reenvian menos: 185.000 de 276.000, 90.000 de 185.000.
    rasgos = await _rasgos(base_demo)
    assert rasgos["7104-2288"]["proporcion_pb"] == 6702
    assert rasgos["6001-3344"]["proporcion_pb"] == 0


@pytest.mark.asyncio
async def prueba_merge_es_idempotente_y_borra_lo_de_corridas_anteriores(
    base_demo: Any,
) -> None:
    await _recalcular_demo(base_demo)
    primera = await _rasgos(base_demo)
    await _recalcular_demo(base_demo)
    segunda = await _rasgos(base_demo)

    def sin_marca(docs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {c: {k: v for k, v in d.items() if k != "calculado_en"} for c, d in docs.items()}

    assert sin_marca(primera) == sin_marca(segunda)

    # Una corrida mas tarde y de otro rango: lo que ya no tiene movimiento desaparece.
    await _repo(base_demo).recalcular(
        CORTE_DEMO + timedelta(days=2), ventana_minutos=15, horizonte=timedelta(hours=24)
    )
    assert await _rasgos(base_demo) == {}
    corrida = await base_demo["deteccion_estado"].find_one({"_id": NOMBRE_RASGOS})
    assert corrida["cuentas"] == 0
    assert corrida["corte"] == CORTE_DEMO + timedelta(days=2)


@pytest.mark.asyncio
async def prueba_el_validador_rechaza_un_rasgo_con_dinero_flotante(base_demo: Any) -> None:
    await _recalcular_demo(base_demo)
    doc = await base_demo["deteccion_estado"].find_one({"_id": id_rasgo(MULA)})
    doc["_id"] = id_rasgo("7777-7777")
    doc["cuenta"] = "7777-7777"
    doc["monto_recibido_crc"] = 280000.0

    with pytest.raises(WriteError):
        await base_demo["deteccion_estado"].insert_one(doc)


@pytest.mark.asyncio
async def prueba_los_procedimientos_no_calculan_con_la_regla_apagada(base_demo: Any) -> None:
    repo = _repo(base_demo)
    await base_demo["reglas_deteccion"].update_one({"tipo": "red_mulas"}, {"$set": {"activa": False}})

    assert await repo.recalcular_si_activa(CORTE_DEMO) == 0
    assert await base_demo["deteccion_estado"].count_documents({}) == 0

    await base_demo["reglas_deteccion"].update_one({"tipo": "red_mulas"}, {"$set": {"activa": True}})
    assert await repo.recalcular_si_activa(CORTE_DEMO) == 7
    assert await repo.ventana_de_la_regla(solo_activa=True) == 15


# ---------------------------------------------------------------------------
# R-11 en D1
# ---------------------------------------------------------------------------


def _hacia_la_mula(identificador: str, segundos: int = 30) -> Transaccion:
    """Otra victima transfiere a 6033-9001 justo despues del corte."""
    return Transaccion(
        _id=identificador,
        cuenta_origen="CTA-002",
        sinpe_origen="6210-3387",
        cuenta_destino=MULA,
        monto_crc=250_000,
        fecha=CORTE_DEMO + timedelta(seconds=segundos),
    )


async def _r11_activa(base: Any) -> list[Regla]:
    await base["reglas_deteccion"].update_one({"tipo": "red_mulas"}, {"$set": {"activa": True}})
    docs = await base["reglas_deteccion"].find({"activa": True}).to_list(length=None)
    return [Regla.model_validate(d) for d in docs]


async def _contexto(base: Any, transaccion: Transaccion, reglas: list[Regla]) -> ContextoEvaluacion:
    origen = DatosOrigen(cuenta_sinpe="6210-3387", cliente_id="CLI-002", cliente_nombre="x")
    return await RepoDeteccion(base).armar_contexto(transaccion, origen, reglas)


@pytest.mark.asyncio
async def prueba_sin_corrida_de_d3_r11_queda_no_evaluada(base_demo: Any) -> None:
    reglas = await _r11_activa(base_demo)
    transaccion = _hacia_la_mula("TXN-100")

    contexto = await _contexto(base_demo, transaccion, reglas)

    assert contexto.rasgo_red is None
    repo = RepoDeteccion(base_demo)
    evaluacion = evaluar(transaccion, contexto, reglas, await repo.leer_politica_vigente())
    [no_evaluada] = [n for n in evaluacion.reglas_no_evaluadas if n.codigo == "R-11"]
    assert "no hay rasgos de grafo" in no_evaluada.motivo
    assert "R-11" not in [d.codigo for d in evaluacion.reglas_disparadas]


@pytest.mark.asyncio
async def prueba_con_la_regla_apagada_no_se_lee_el_rasgo(base_demo: Any) -> None:
    await _recalcular_demo(base_demo)
    await base_demo["reglas_deteccion"].update_one({"tipo": "red_mulas"}, {"$set": {"activa": False}})
    reglas = [
        Regla.model_validate(d)
        for d in await base_demo["reglas_deteccion"].find({"activa": True}).to_list(length=None)
    ]
    assert "red_mulas" not in {r.tipo for r in reglas}

    contexto = await _contexto(base_demo, _hacia_la_mula("TXN-100"), reglas)

    assert contexto.rasgo_red is None


@pytest.mark.asyncio
async def prueba_la_siguiente_transaccion_hacia_la_mula_dispara_r11(base_demo: Any) -> None:
    await _recalcular_demo(base_demo)
    reglas = await _r11_activa(base_demo)
    transaccion = _hacia_la_mula("TXN-100")
    politica = await RepoDeteccion(base_demo).leer_politica_vigente()

    contexto = await _contexto(base_demo, transaccion, reglas)
    evaluacion = evaluar(transaccion, contexto, reglas, politica)

    rasgo = contexto.rasgo_red
    assert rasgo is not None
    assert (rasgo.monto_recibido_crc, rasgo.monto_reenviado_crc) == (280_000, 276_000)
    [r11] = [d for d in evaluacion.reglas_disparadas if d.codigo == "R-11"]
    assert r11.version == 1
    assert "recibio 280.000 CRC y reenvio 276.000 CRC (98,6 %) en 4 minutos" in r11.explicacion

    # Un rasgo que ya envejecio no dispara: pasados 61 segundos del corte, no evaluada.
    tarde = _hacia_la_mula("TXN-101", segundos=61)
    evaluacion = evaluar(tarde, await _contexto(base_demo, tarde, reglas), reglas, politica)
    assert "R-11" not in [d.codigo for d in evaluacion.reglas_disparadas]
    assert any(
        n.codigo == "R-11" and "viejo" in n.motivo for n in evaluacion.reglas_no_evaluadas
    )


async def _esperar(condicion: Callable[[], Awaitable[bool]], que: str) -> None:
    limite = asyncio.get_running_loop().time() + LIMITE_SEGUNDOS
    while not await condicion():
        if asyncio.get_running_loop().time() > limite:
            raise AssertionError(f"pasaron {LIMITE_SEGUNDOS} s esperando {que}")
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def prueba_d1_de_punta_a_punta_levanta_la_alerta_con_r11(base_demo: Any) -> None:
    """D1 real: change stream -> contexto con el rasgo -> evaluador -> alerta v2 con R-11."""
    await _recalcular_demo(base_demo)
    await _r11_activa(base_demo)
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(RepoDeteccion(base_demo), supervisor, ajustes=AJUSTES_D1))

    async def abierto() -> bool:
        return supervisor.informe().tarea(NOMBRE_D1).eventos_procesados >= 1

    try:
        await _esperar(abierto, "el primer latido de D1")
        await base_demo["transacciones"].insert_one(
            a_entero_64(_hacia_la_mula("TXN-100").a_documento())
        )

        async def hay_alerta() -> bool:
            return await base_demo["alertas"].count_documents({"transaccion_id": "TXN-100"}) == 1

        await _esperar(hay_alerta, "la alerta de TXN-100")
    finally:
        await supervisor.detener()

    alerta = await base_demo["alertas"].find_one({"transaccion_id": "TXN-100"})
    r11 = next(r for r in alerta["reglas_disparadas"] if r["codigo"] == "R-11")
    assert (r11["regla_id"], r11["version"], r11["peso"]) == ("REG-006", 1, 20)
    assert "98,6 %" in r11["explicacion"]
    assert alerta["cuenta_destino"] == MULA


# ---------------------------------------------------------------------------
# El plan
# ---------------------------------------------------------------------------


def _etapa_cursor(explicacion: dict[str, Any]) -> dict[str, Any]:
    return next(e["$cursor"] for e in explicacion["stages"] if "$cursor" in e)


def _contiene(nodo: Any, clave: str, valor: str) -> bool:
    """Si `clave: valor` aparece en cualquier rincon del plan (la forma cambia por motor)."""
    if isinstance(nodo, dict):
        return nodo.get(clave) == valor or any(_contiene(v, clave, valor) for v in nodo.values())
    if isinstance(nodo, list):
        return any(_contiene(v, clave, valor) for v in nodo)
    return False


@pytest.mark.asyncio
async def prueba_el_match_lee_el_horizonte_por_indice_y_el_grafo_por_el_suyo(
    base_generada: Any,
) -> None:
    """`nReturned` es lo que la etapa entrega al resto del pipeline (las cuentas que
    recibieron algo, ya agrupadas) y `totalDocsExamined` lo que se leyo para llegar: las
    transferencias del horizonte, ni una mas. Sin `idx_transacciones_fecha` serian todas."""
    repo = _repo(base_generada)
    transacciones = base_generada["transacciones"]
    total = await transacciones.count_documents({})
    ultima = await transacciones.find_one({}, sort=[("fecha", -1)])
    corte = ultima["fecha"] + timedelta(seconds=1)
    horizonte = timedelta(hours=24)
    rango = {"fecha": {"$gte": corte - horizonte, "$lt": corte}}
    en_el_horizonte = await transacciones.count_documents(rango)
    destinos = len(await transacciones.distinct("cuenta_destino", rango))

    explicacion = await repo.explicar(corte, ventana_minutos=15, horizonte=horizonte)
    cursor = _etapa_cursor(explicacion)
    estadisticas = cursor["executionStats"]
    print(
        f"\n$match del horizonte: {total} documentos en la coleccion, "
        f"totalKeysExamined={estadisticas['totalKeysExamined']} "
        f"totalDocsExamined={estadisticas['totalDocsExamined']} "
        f"nReturned={estadisticas['nReturned']} (cuentas)"
    )

    plan = cursor["queryPlanner"]["winningPlan"]
    assert _contiene(plan, "stage", "IXSCAN") and not _contiene(plan, "stage", "COLLSCAN")
    assert _contiene(plan, "indexName", "idx_transacciones_fecha")
    assert 0 < en_el_horizonte < total
    assert estadisticas["totalKeysExamined"] == en_el_horizonte
    assert estadisticas["totalDocsExamined"] == en_el_horizonte
    assert estadisticas["nReturned"] == destinos

    antes = await repo.usos_de_indices()
    await repo.recalcular(corte, ventana_minutos=15, horizonte=horizonte)
    despues = await repo.usos_de_indices()
    grafo = "idx_transacciones_sinpe_origen_fecha"
    print(f"$graphLookup: {despues[grafo] - antes[grafo]} busquedas por {grafo}")
    assert despues[grafo] - antes[grafo] >= destinos, "una busqueda por cuenta, al menos"
    assert despues["idx_transacciones_fecha"] == antes["idx_transacciones_fecha"] + 1


# ---------------------------------------------------------------------------
# demo-cadena-viva (D-68)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_cadena_viva_deja_el_rasgo_en_986_y_la_transferencia_dispara_r11(
    base_demo: Any,
) -> None:
    from app.deteccion.demo_cadena import ejecutar

    antes = await base_demo["transacciones"].count_documents({})

    resultado = await ejecutar(base_demo, disparar=True)

    rasgo = resultado["rasgo"]
    assert (rasgo.monto_recibido_crc, rasgo.monto_reenviado_crc) == (280_000, 276_000)
    assert rasgo.proporcion_pb == 9857 and rasgo.saltos == 3 and rasgo.minutos_reenvio == 4
    assert await base_demo["transacciones"].count_documents({}) == antes + 5, "4 de cadena y 1 de disparo"

    disparo = resultado["disparo"]
    reglas = await _r11_activa(base_demo)
    politica = await RepoDeteccion(base_demo).leer_politica_vigente()
    contexto = await _contexto(base_demo, disparo, reglas)
    evaluacion = evaluar(disparo, contexto, reglas, politica)
    [r11] = [d for d in evaluacion.reglas_disparadas if d.codigo == "R-11"]
    assert "98,6 %" in r11.explicacion


@pytest.mark.asyncio
async def prueba_el_modulo_se_puede_correr_varias_veces_y_no_borra_nada(base_demo: Any) -> None:
    entorno = {**os.environ, "CENTINELA_MONGO__BASE": BASE_RASGOS}
    antes = {d["_id"] async for d in base_demo["transacciones"].find({}, {"_id": 1})}

    def correr() -> str:
        salida = subprocess.run(
            [sys.executable, "-m", "app.deteccion.demo_cadena", "--disparar"],
            capture_output=True, text=True, env=entorno, timeout=60, check=False,
        )  # fmt: skip
        assert salida.returncode == 0, salida.stderr
        return salida.stdout

    primera = await asyncio.to_thread(correr)
    segunda = await asyncio.to_thread(correr)

    assert "proporcion 98,6 %, 4 minutos, 3 saltos" in primera
    assert "transferencia de disparo: TXN-" in primera
    despues = {d["_id"] async for d in base_demo["transacciones"].find({}, {"_id": 1})}
    assert antes < despues and len(despues - antes) == 10, "ids nuevos en cada corrida"
    assert min(despues - antes) == "TXN-900001" and max(despues - antes) == "TXN-900010"
    # Repetir dentro del horizonte diluye la proporcion: la transferencia de disparo de la
    # primera corrida es dinero recibido por 6033-9001 que no se reenvio.
    assert "rasgo de 6033-9001" in segunda and "proporcion 98,6 %" not in segunda
