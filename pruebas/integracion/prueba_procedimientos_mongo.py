"""T-76 contra MongoDB de verdad: los procedimientos de mongosh y sus equivalentes en Python.

Rubro 9 de la rubrica: `scripts/procedimientos/*.js` corridos con `mongosh --file`. Cada uno
se comprueba contra la funcion de Python que hace lo mismo:

- 01 indicadores, 02 rasgos de grafo y 03 perfiles: sobre los MISMOS datos (el generador con
  semilla fija, mas alertas insertadas a mano), el script y la funcion de Python escriben
  documentos identicos byte a byte (mismo orden de campos y mismos tipos BSON), salvo la marca
  de tiempo del calculo.
- 04 efectividad: la tabla del script contiene cada linea de la que imprime
  `formatear_tabla`, y el F1 sale de las mismas cuentas.
- 05 map reduce: los dos resultados coinciden entre si y con un calculo hecho en Python.

Base propia, `antifraude_pruebas_procedimientos`, que se tira al terminar (T-34: ninguna
prueba lee `antifraude`). Los validadores salen de `BASE_SEMILLA`: si un script escribiera lo
que el contrato rechaza, `$merge` falla y la prueba con el.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import bson
import pytest
import pytest_asyncio
from bson import json_util

from app.deteccion import evaluar
from app.deteccion.efectividad import calcular_efectividad, formatear_tabla
from app.deteccion.perfiles import calcular_perfil
from app.generador.ejecucion import ejecutar
from app.modelos import Regla, Transaccion
from app.nucleo.config import Ajustes, AjustesGenerador, AjustesRasgosGrafo
from app.repos.carga_demo import a_entero_64, cargar_demo
from app.repos.deteccion import DatosOrigen, RepoDeteccion
from app.repos.efectividad import RepoEfectividad
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.generador import RepoGenerador
from app.repos.indicadores import RepoIndicadores
from app.repos.perfiles import RepoPerfiles
from app.repos.rasgos_grafo import RepoRasgosGrafo
from pruebas.conftest import BASE_SEMILLA, RUTA_RAIZ, saltar_o_fallar
from pruebas.dobles.datos_demo import DATOS_DEMO, TRANSACCIONES

pytestmark = pytest.mark.mongo

BASE_PROC = "antifraude_pruebas_procedimientos"
PROCEDIMIENTOS = RUTA_RAIZ / "scripts" / "procedimientos"
VALIDADORES = (
    "clientes",
    "cuentas",
    "perfiles_comportamiento",
    "listas_riesgo",
    "transacciones",
    "alertas",
    "deteccion_estado",
    "indicadores_diarios",
)
CATALOGO = ("reglas_deteccion", "politica_deteccion")
DE_IDENTIDAD = ("clientes", "cuentas", "perfiles_comportamiento", "listas_riesgo")
INDICES = {
    "transacciones": [
        ("idx_transacciones_fecha", [("fecha", 1)]),
        ("idx_transacciones_cuenta_origen_fecha", [("cuenta_origen", 1), ("fecha", 1)]),
        ("idx_transacciones_sinpe_origen_fecha", [("sinpe_origen", 1), ("fecha", 1)]),
        ("idx_transacciones_cuenta_destino", [("cuenta_destino", 1)]),
    ],
}
GENERADOR = AjustesGenerador(clientes=200, transacciones=5000, semilla=76)
ESTADOS = ("nueva", "en_revision", "confirmada", "descartada", "escalada")
SEVERIDADES = ("MEDIA", "ALTA", "CRITICA")


# ---------------------------------------------------------------------------
# Preparacion
# ---------------------------------------------------------------------------


def _mongosh(ajustes: Ajustes, script: str, **variables: Any) -> str:
    """Corre un procedimiento con `mongosh --file` sobre la base de la prueba."""
    mongosh = shutil.which("mongosh")
    if mongosh is None:
        saltar_o_fallar("mongosh no esta en el PATH; hace falta para correr los procedimientos")
    asignaciones = [f"BASE_CENTINELA = {json.dumps(BASE_PROC)}"]
    asignaciones += [f"{nombre} = {json.dumps(valor)}" for nombre, valor in variables.items()]
    resultado = subprocess.run(
        [
            mongosh,
            ajustes.mongo.uri,
            "--quiet",
            "--eval",
            "; ".join(asignaciones) + "; void 0",
            "--file",
            str(PROCEDIMIENTOS / script),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    return resultado.stdout


async def _mongosh_async(ajustes: Ajustes, script: str, **variables: Any) -> str:
    return await asyncio.to_thread(_mongosh, ajustes, script, **variables)


def _alerta(n: int, transaccion: dict[str, Any], estado: str, agente: str | None) -> dict[str, Any]:
    """Una alerta v1 sobre `transaccion`, creada un minuto despues de ella."""
    fecha = transaccion["fecha"].replace(tzinfo=UTC) + timedelta(minutes=1)
    historial = [{"accion": "nueva", "agente_id": None, "fecha": fecha}]
    if estado in ("confirmada", "descartada", "escalada"):
        cerrada = fecha + timedelta(minutes=7 + n % 5)
        historial.append({"accion": estado, "agente_id": agente, "fecha": cerrada})
    documento = {
        "_id": f"ALR-{n:06d}",
        "transaccion_id": transaccion["_id"],
        "cuenta_origen": transaccion["sinpe_origen"],
        "cuenta_destino": transaccion["cuenta_destino"],
        "monto_crc": transaccion["monto_crc"],
        "moneda": "CRC",
        "cliente_nombre": "Cliente de prueba",
        "reglas_disparadas": ["REG-001"],
        "puntaje": 40 + n % 50,
        "severidad": SEVERIDADES[n % 3],
        "estado": estado,
        "fecha_creacion": fecha,
        "historial": historial,
        "esquema_version": 1,
    }
    if agente is not None:
        documento["agente_id"] = agente
    return documento


@pytest_asyncio.fixture
async def base_proc(base_mongo: Any) -> AsyncIterator[Any]:
    """5.000 transacciones del generador (semilla fija) y alertas sobre algunas de ellas."""
    cliente = base_mongo.client
    trabajo = cliente[BASE_SEMILLA]
    validadores = await obtener_validadores(trabajo)
    faltan = [nombre for nombre in VALIDADORES if nombre not in validadores]
    if faltan:
        saltar_o_fallar(f"sin validador en {BASE_SEMILLA} para {faltan}")
    await cliente.drop_database(BASE_PROC)
    base = cliente[BASE_PROC]
    for nombre in VALIDADORES:
        await recrear_con_validador(base, nombre, validadores[nombre])
    for coleccion, indices in INDICES.items():
        for nombre, clave in indices:
            await base[coleccion].create_index(clave, name=nombre)
    await base["alertas"].create_index(
        "transaccion_id", unique=True, name="idx_alertas_transaccion_id"
    )
    for nombre in CATALOGO:
        documentos = await trabajo[nombre].find({}).to_list(length=None)
        if not documentos:
            saltar_o_fallar(f"{BASE_SEMILLA}.{nombre} vacia; correr 03-catalogos.js")
        await base[nombre].insert_many(documentos)
    try:
        await ejecutar(RepoGenerador(base), GENERADOR, limpiar=False)
        transacciones = await base["transacciones"].find({}).sort("_id", 1).to_list(length=None)
        fraude = [t for t in transacciones if t["simulacion"]["fraude"]]
        legitimas = [t for t in transacciones if not t["simulacion"]["fraude"]]
        # La mitad del fraude y una de cada 25 legitimas tienen alerta, en los cinco estados.
        elegidas = fraude[::2] + legitimas[::25]
        await base["alertas"].insert_many(
            [
                _alerta(n, t, ESTADOS[n % 5], "AGT-001" if n % 3 else None)
                for n, t in enumerate(elegidas, start=1)
            ]
        )
        yield base
    finally:
        await cliente.drop_database(BASE_PROC)


async def _corte_de(base: Any) -> datetime:
    """Un segundo despues de la ultima transaccion, truncado a milisegundo."""
    ultima = await base["transacciones"].find({}).sort("fecha", -1).limit(1).to_list(length=1)
    return ultima[0]["fecha"].replace(tzinfo=UTC) + timedelta(seconds=1)


async def _documentos(base: Any, coleccion: str, filtro: dict[str, Any], quitar: tuple[str, ...]):
    docs = await base[coleccion].find(filtro).sort("_id", 1).to_list(length=None)
    for doc in docs:
        for campo in quitar:
            doc.pop(campo, None)
    return docs


def _identicos(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> None:
    """Mismos documentos, y ademas los mismos bytes: mismo orden de campos y tipos BSON."""
    assert a == b
    # JSON canonico: conserva el tipo BSON de cada numero y el orden de los campos, y si
    # difiere, el mensaje dice en cual documento.
    canonico = json_util.CANONICAL_JSON_OPTIONS
    for uno, otro in zip(a, b, strict=True):
        assert json_util.dumps(uno, json_options=canonico) == json_util.dumps(
            otro, json_options=canonico
        ), uno["_id"]
    assert [bson.encode(d) for d in a] == [bson.encode(d) for d in b]


# ---------------------------------------------------------------------------
# 01 indicadores
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_01_indicadores_identicos_a_la_funcion_de_python(
    base_proc: Any, ajustes: Ajustes
) -> None:
    coleccion = base_proc["indicadores_diarios"]
    desde, hasta = date(2026, 10, 20), date(2026, 11, 28)  # hay dias con alertas y dias en ceros

    salida = await _mongosh_async(
        ajustes, "01-recalcular-indicadores.js", DESDE=desde.isoformat(), HASTA=hasta.isoformat()
    )
    del_script = await _documentos(base_proc, "indicadores_diarios", {}, ("calculado_en",))
    assert "40 dia(s) recalculados" in salida

    await coleccion.delete_many({})
    await RepoIndicadores(base_proc).recalcular(desde, hasta)
    de_python = await _documentos(base_proc, "indicadores_diarios", {}, ("calculado_en",))

    assert len(del_script) == 40
    assert any(d["total_alertas"] > 0 for d in del_script), "la comparacion no puede ser trivial"
    assert any(d["total_alertas"] == 0 for d in del_script), "faltan dias en ceros"
    assert any(d["decision_cantidad"] > 0 for d in del_script)
    _identicos(del_script, de_python)

    # Idempotente: correrlo de nuevo no cambia nada (salvo la marca) ni agrega documentos.
    await _mongosh_async(
        ajustes, "01-recalcular-indicadores.js", DESDE=desde.isoformat(), HASTA=hasta.isoformat()
    )
    _identicos(await _documentos(base_proc, "indicadores_diarios", {}, ("calculado_en",)), de_python)


# ---------------------------------------------------------------------------
# 02 rasgos de grafo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_02_rasgos_identicos_a_la_funcion_de_python(
    base_proc: Any, ajustes: Ajustes
) -> None:
    corte = await _corte_de(base_proc)
    horizonte = timedelta(days=60)
    ventana = 15
    quitar = ("calculado_en", "actualizado_en")

    salida = await _mongosh_async(
        ajustes,
        "02-recalcular-rasgos-grafo.js",
        CORTE=corte.isoformat(),
        HORAS=horizonte.total_seconds() / 3600,
        VENTANA_MINUTOS=ventana,
        VIGENCIA_SEGUNDOS=60,
    )
    del_script = await _documentos(base_proc, "deteccion_estado", {}, quitar)
    assert "cuenta(s) recalculadas" in salida

    await base_proc["deteccion_estado"].delete_many({})
    cuentas = await RepoRasgosGrafo(base_proc, AjustesRasgosGrafo(vigencia_segundos=60)).recalcular(
        corte, ventana_minutos=ventana, horizonte=horizonte
    )
    de_python = await _documentos(base_proc, "deteccion_estado", {}, quitar)

    assert cuentas > 100 and len(del_script) == cuentas + 1, "un rasgo por cuenta mas la corrida"
    assert any(d.get("saltos", 0) >= 3 for d in del_script), "hay cadenas de verdad"
    assert any(d.get("proporcion_pb", 0) > 0 for d in del_script)
    _identicos(del_script, de_python)

    # Sin VENTANA_MINUTOS el script toma la de la regla red_mulas del catalogo.
    regla = await base_proc["reglas_deteccion"].find_one({"tipo": "red_mulas"})
    await _mongosh_async(
        ajustes, "02-recalcular-rasgos-grafo.js", CORTE=corte.isoformat(), HORAS=1440
    )
    corrida = await base_proc["deteccion_estado"].find_one({"_id": "d3_rasgos_grafo"})
    assert corrida["ventana_minutos"] == regla["parametros"]["ventana_minutos"]


# ---------------------------------------------------------------------------
# 03 perfiles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_03_perfiles_identicos_a_la_funcion_de_python_y_a_la_definicion(
    base_proc: Any, ajustes: Ajustes
) -> None:
    perfiles = base_proc["perfiles_comportamiento"]
    corte = await _corte_de(base_proc)

    await perfiles.delete_many({})
    salida = await _mongosh_async(
        ajustes, "03-recalcular-perfiles.js", CORTE=corte.isoformat(), DIAS=90
    )
    del_script = await _documentos(base_proc, "perfiles_comportamiento", {}, ())
    assert "perfil(es) recalculados (todos los clientes, 90 dias)" in salida

    await perfiles.delete_many({})
    total = await RepoPerfiles(base_proc).recalcular(corte, dias=90)
    de_python = await _documentos(base_proc, "perfiles_comportamiento", {}, ())

    assert total == len(del_script) > 150
    _identicos(del_script, de_python)

    # Y los dos son la DEFINICION: el calculo puro de `app/deteccion/perfiles.py`, a partir de
    # las transacciones que cuentan (las que envio el cliente, sin alerta viva).
    cuentas = {c["_id"]: c["cliente_id"] async for c in base_proc["cuentas"].find({})}
    con_alerta_viva = {
        a["transaccion_id"]
        async for a in base_proc["alertas"].find({"estado": {"$ne": "descartada"}})
    }
    descartadas = await base_proc["alertas"].count_documents({"estado": "descartada"})
    assert con_alerta_viva and descartadas, "hay alertas vivas y descartadas"
    por_cliente: dict[str, list[tuple[int, str, datetime]]] = defaultdict(list)
    async for t in base_proc["transacciones"].find(
        {"fecha": {"$gte": corte - timedelta(days=90), "$lt": corte}}
    ):
        if t["_id"] not in con_alerta_viva:
            por_cliente[cuentas[t["cuenta_origen"]]].append(
                (int(t["monto_crc"]), t["cuenta_destino"], t["fecha"].replace(tzinfo=UTC))
            )
    guardados = {d["_id"]: d for d in de_python}
    assert set(guardados) == set(por_cliente)
    for cliente_id, transferencias in por_cliente.items():
        esperado = calcular_perfil(transferencias)
        doc = guardados[cliente_id]
        obtenido = {
            campo: (int(doc[campo]) if campo.endswith("_crc") else doc[campo])
            for campo in esperado
        }
        assert obtenido == esperado, cliente_id
    # Dinero long, conteo int32, esquema_version 1: lo exige el validador y lo comprueba `$merge`.
    assert await perfiles.count_documents({"promedio_crc": {"$not": {"$type": "long"}}}) == 0
    assert await perfiles.count_documents({"num_transferencias": {"$not": {"$type": "int"}}}) == 0
    assert await perfiles.count_documents({"esquema_version": {"$ne": 1}}) == 0


@pytest.mark.asyncio
async def prueba_03_un_solo_cliente_y_reejecutable_sin_duplicar(
    base_proc: Any, ajustes: Ajustes
) -> None:
    perfiles = base_proc["perfiles_comportamiento"]
    corte = await _corte_de(base_proc)

    await perfiles.delete_many({})
    await _mongosh_async(
        ajustes, "03-recalcular-perfiles.js", CORTE=corte.isoformat(), CLIENTE="CLI-0001", DIAS=30
    )
    del_script = await _documentos(base_proc, "perfiles_comportamiento", {}, ())
    await perfiles.delete_many({})
    uno = await RepoPerfiles(base_proc).recalcular(corte, dias=30, cliente_id="CLI-0001")
    de_python = await _documentos(base_proc, "perfiles_comportamiento", {}, ())
    assert uno == 1 and [d["_id"] for d in de_python] == ["CLI-0001"]
    _identicos(del_script, de_python)

    # Reejecutarlo no duplica ni cambia nada, y un cliente sin cuentas no hace nada.
    await RepoPerfiles(base_proc).recalcular(corte, dias=30, cliente_id="CLI-0001")
    await _mongosh_async(
        ajustes, "03-recalcular-perfiles.js", CORTE=corte.isoformat(), CLIENTE="CLI-0001", DIAS=30
    )
    _identicos(await _documentos(base_proc, "perfiles_comportamiento", {}, ()), de_python)
    assert await RepoPerfiles(base_proc).recalcular(corte, cliente_id="CLI-9999") == 0


@pytest.mark.asyncio
async def prueba_03_las_alertas_vivas_no_entran_al_perfil_y_las_descartadas_si(
    base_proc: Any,
) -> None:
    """Una alerta viva saca su transaccion del perfil; una descartada (falso positivo) no."""
    corte = await _corte_de(base_proc)
    cuentas = {c["_id"]: c["cliente_id"] async for c in base_proc["cuentas"].find({})}
    una = await base_proc["alertas"].find_one({"estado": "nueva"})
    transaccion = await base_proc["transacciones"].find_one({"_id": una["transaccion_id"]})
    cliente_id = cuentas[transaccion["cuenta_origen"]]
    repo = RepoPerfiles(base_proc)

    await repo.recalcular(corte, cliente_id=cliente_id)
    [viva] = await repo.leer_perfiles(cliente_id)
    await base_proc["alertas"].update_one({"_id": una["_id"]}, {"$set": {"estado": "descartada"}})
    await repo.recalcular(corte, cliente_id=cliente_id)
    [descartada] = await repo.leer_perfiles(cliente_id)

    assert descartada["num_transferencias"] == viva["num_transferencias"] + 1
    assert descartada["maximo_crc"] >= viva["maximo_crc"]


@pytest_asyncio.fixture
async def base_demo(base_mongo: Any) -> AsyncIterator[Any]:
    """El demo de la defensa: identidad, catalogo, TXN-001..007 y las alertas (ALR-001)."""
    cliente = base_mongo.client
    trabajo = cliente[BASE_SEMILLA]
    validadores = await obtener_validadores(trabajo)
    await cliente.drop_database(BASE_PROC)
    base = cliente[BASE_PROC]
    for nombre in VALIDADORES:
        await recrear_con_validador(base, nombre, validadores[nombre])
    await base["alertas"].create_index(
        "transaccion_id", unique=True, name="idx_alertas_transaccion_id"
    )
    for nombre in CATALOGO:
        await base[nombre].insert_many(await trabajo[nombre].find({}).to_list(length=None))
    try:
        await cargar_demo(base, {nombre: DATOS_DEMO[nombre] for nombre in (*DE_IDENTIDAD, "alertas")})
        await base["transacciones"].insert_many(
            [a_entero_64(t.a_documento()) for t in TRANSACCIONES]
        )
        yield base
    finally:
        await cliente.drop_database(BASE_PROC)


async def _evaluar_txn_001(base: Any) -> tuple[int, str]:
    reglas = [
        Regla.model_validate(d)
        async for d in base["reglas_deteccion"].find({"activa": True})
    ]
    repo = RepoDeteccion(base)
    documento = await base["transacciones"].find_one({"_id": "TXN-001"})
    transaccion = Transaccion.model_validate(documento)
    origen = DatosOrigen(
        cuenta_sinpe="8712-4455", cliente_id="CLI-001", cliente_nombre="Maria Rodriguez Vargas"
    )
    contexto = await repo.armar_contexto(transaccion, origen, reglas)
    evaluacion = evaluar(transaccion, contexto, reglas, await repo.leer_politica_vigente())
    return evaluacion.puntaje, str(evaluacion.severidad)


@pytest.mark.asyncio
async def prueba_03_recalcular_perfiles_no_cambia_el_caso_de_la_demo(base_demo: Any) -> None:
    """TXN-001 sigue dando 75 y CRITICA, y el perfil de CLI-001 queda intacto.

    TXN-001 tiene su alerta (ALR-001): una alerta viva saca a la transaccion del perfil de
    Maria, asi que su propio fraude no la vuelve normal. Los otros tres clientes del demo, que
    no tenian perfil, ahora lo tienen.
    """
    antes = await _evaluar_txn_001(base_demo)
    perfil_antes = await base_demo["perfiles_comportamiento"].find_one({"_id": "CLI-001"})
    assert antes[0] == 75 and "CRITICA" in antes[1]

    corte_demo = datetime(2026, 10, 1, tzinfo=UTC)
    await RepoPerfiles(base_demo).recalcular(corte_demo, dias=90)

    assert await _evaluar_txn_001(base_demo) == antes
    assert await base_demo["perfiles_comportamiento"].find_one({"_id": "CLI-001"}) == perfil_antes
    assert await base_demo["perfiles_comportamiento"].count_documents({}) > 1

    # El caso contrario, para que quede escrito: sin la alerta, TXN-001 entra al perfil de
    # Maria, su promedio sube y R-01 deja de dispararse. Por eso el recalculo no es automatico.
    await base_demo["alertas"].delete_many({"transaccion_id": "TXN-001"})
    await RepoPerfiles(base_demo).recalcular(corte_demo, dias=90, cliente_id="CLI-001")
    perfil = await base_demo["perfiles_comportamiento"].find_one({"_id": "CLI-001"})
    assert perfil["promedio_crc"] == 280_000 and perfil != perfil_antes
    assert (await _evaluar_txn_001(base_demo))[0] < 75


# ---------------------------------------------------------------------------
# 04 efectividad
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_04_la_tabla_de_efectividad_coincide_con_la_de_python(
    base_proc: Any, ajustes: Ajustes
) -> None:
    salida = await _mongosh_async(ajustes, "04-reporte-efectividad.js")

    filas, alertas_totales = await RepoEfectividad(base_proc).leer_filas_etiquetadas()
    reporte = calcular_efectividad(filas, alertas_totales=alertas_totales)
    del_script = salida.splitlines()
    for linea in formatear_tabla(reporte).splitlines():
        assert linea in del_script, f"falta en la salida del script: {linea!r}"

    # F1 con las mismas cuentas que las otras dos metricas.
    d, p, f = reporte.detectados, reporte.falsos_positivos, reporte.perdidos
    f1 = round(2 * d / (2 * d + p + f), 4)
    assert reporte.detectados > 0 and reporte.perdidos > 0 and reporte.falsos_positivos > 0
    assert f"F1: {100 * f1:.1f} %" in del_script


# ---------------------------------------------------------------------------
# 05 map reduce
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_05_map_reduce_y_aggregate_coinciden(base_proc: Any, ajustes: Ajustes) -> None:
    salida = await _mongosh_async(ajustes, "05-map-reduce-comparativo.js")

    esperado: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    async for alerta in base_proc["alertas"].find({}):
        esperado[alerta["cuenta_destino"]][0] += int(alerta["monto_crc"])
        esperado[alerta["cuenta_destino"]][1] += 1
    assert len(esperado) > 20

    assert "coinciden: SI" in salida
    assert "mapReduce esta obsoleto" in salida
    assert f"mapReduce (out inline): {len(esperado)} cuenta(s) destino" in salida
    assert f"aggregate ($group): {len(esperado)} cuenta(s) destino" in salida
    # Las dos tablas traen, por cuenta, el monto y la cantidad calculados aparte en Python.
    for cuenta, (monto, cantidad) in esperado.items():
        linea = f"  {cuenta}  monto_crc={monto:>9}  alertas={cantidad}"
        assert salida.count(linea) == 2, linea
    # Con out inline no se escribio ninguna coleccion nueva.
    assert not [n for n in await base_proc.list_collection_names() if "map" in n.lower()]


def prueba_los_seis_scripts_traen_encabezado_y_no_tienen_contrasenas() -> None:
    nombres = sorted(p.name for p in Path(PROCEDIMIENTOS).glob("*.js"))
    assert nombres == [
        "01-recalcular-indicadores.js",
        "02-recalcular-rasgos-grafo.js",
        "03-recalcular-perfiles.js",
        "04-reporte-efectividad.js",
        "05-map-reduce-comparativo.js",
        "06-bitacora.js",
    ]
    for nombre in nombres:
        texto = (PROCEDIMIENTOS / nombre).read_text(encoding="utf-8")
        for seccion in ("Que hace", "Entradas", "Escribe", "Como correrlo", "Como verificarlo"):
            assert seccion in texto.split("const ")[0], f"{nombre}: falta '{seccion}' en el encabezado"
        assert "pwd" not in texto
