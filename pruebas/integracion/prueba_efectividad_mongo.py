"""H-10 contra MongoDB de verdad: generador + D1 + reporte de efectividad.

El escenario es el de la demo: el generador escribe 200 clientes y 5.000 transacciones
mientras D1 esta escuchando, D1 evalua cada una con el catalogo real de reglas y la
politica de H-07, y despues el reporte cruza las alertas con la etiqueta oculta.

Base propia, `antifraude_pruebas_efectividad`, que se tira al terminar. Los validadores
y el catalogo se copian de `antifraude` (los siembra `scripts/semilla/`), igual que en
`prueba_d1_mongo.py`: si el generador o D1 escribieran algo que el contrato rechaza, la
prueba falla.

Lo que se comprueba es coherencia, no un numero magico: detectados + perdidos = fraudes
etiquetados, toda alerta cae en una casilla, y la API dice lo mismo que el calculo. Los
numeros reales de la corrida se imprimen (`pytest -s`) para el registro de ajustes.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.deteccion.d1 import NOMBRE_D1, d1
from app.deteccion.efectividad import calcular_efectividad, formatear_tabla
from app.generador.ejecucion import ejecutar, generar_conjunto
from app.main import crear_aplicacion
from app.nucleo.config import AjustesDeteccion, AjustesGenerador, obtener_ajustes
from app.repos.deteccion import RepoDeteccion
from app.repos.efectividad import RepoEfectividad
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.generador import RepoGenerador
from app.supervision import Supervisor
from pruebas.conftest import saltar_o_fallar

pytestmark = pytest.mark.mongo

BASE_DE_TRABAJO = "antifraude"
BASE_EFECTIVIDAD = "antifraude_pruebas_efectividad"
CON_VALIDADOR = (
    "clientes",
    "cuentas",
    "perfiles_comportamiento",
    "listas_riesgo",
    "transacciones",
    "alertas",
    "deteccion_estado",
)
CATALOGO = ("reglas_deteccion", "politica_deteccion")

GENERADOR = AjustesGenerador(clientes=200, transacciones=5000)
AJUSTES_D1 = AjustesDeteccion(
    espera_cursor_ms=100,
    reintentos=1,
    espera_inicial_segundos=0.05,
    espera_maxima_segundos=0.2,
)
LIMITE_SEGUNDOS = 300


@pytest_asyncio.fixture
async def base_efectividad(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    trabajo = cliente[BASE_DE_TRABAJO]
    validadores = await obtener_validadores(trabajo)
    faltan = [nombre for nombre in CON_VALIDADOR if nombre not in validadores]
    if faltan:
        saltar_o_fallar(
            f"sin validador en {BASE_DE_TRABAJO} para {faltan}; correr "
            "scripts/semilla/01-colecciones.js"
        )

    await cliente.drop_database(BASE_EFECTIVIDAD)
    base = cliente[BASE_EFECTIVIDAD]
    for nombre in CON_VALIDADOR:
        await recrear_con_validador(base, nombre, validadores[nombre])
    # Los indices que usan D1 y el reporte, con los nombres de 02-indices.js.
    await base["alertas"].create_index(
        "transaccion_id", unique=True, name="idx_alertas_transaccion_id"
    )
    await base["transacciones"].create_index(
        [("cuenta_origen", 1), ("fecha", -1)], name="idx_transacciones_cuenta_origen_fecha"
    )
    await base["cuentas"].create_index(
        "cuenta_sinpe", unique=True, name="idx_cuentas_sinpe"
    )
    await base["cuentas"].create_index("cliente_id", name="idx_cuentas_cliente")
    await base["listas_riesgo"].create_index(
        "cuenta", unique=True, name="idx_listas_riesgo_cuenta"
    )
    for nombre in CATALOGO:
        documentos = await trabajo[nombre].find({}).to_list(length=None)
        if not documentos:
            saltar_o_fallar(f"{BASE_DE_TRABAJO}.{nombre} vacia; correr 03-catalogos.js")
        await base[nombre].insert_many(documentos)

    try:
        yield base
    finally:
        await cliente.drop_database(BASE_EFECTIVIDAD)


async def _esperar(condicion: Callable[[], Awaitable[bool]], que: str) -> None:
    limite = asyncio.get_running_loop().time() + LIMITE_SEGUNDOS
    while not await condicion():
        if asyncio.get_running_loop().time() > limite:
            raise AssertionError(f"pasaron {LIMITE_SEGUNDOS} s esperando {que}")
        await asyncio.sleep(0.2)


@asynccontextmanager
async def _d1_corriendo(base: Any) -> AsyncIterator[Supervisor]:
    """D1 como en el lifespan; cede el control con el change stream ya abierto."""
    supervisor = Supervisor(tolerancia_segundos=60)
    supervisor.lanzar(NOMBRE_D1, d1(RepoDeteccion(base), supervisor, ajustes=AJUSTES_D1))

    async def abierto() -> bool:
        return supervisor.informe().tarea(NOMBRE_D1).eventos_procesados >= 1

    try:
        await _esperar(abierto, "el primer latido de D1")
        yield supervisor
    finally:
        await supervisor.detener()


@pytest.mark.asyncio
async def prueba_generador_y_d1_dan_un_reporte_coherente(base_efectividad: Any) -> None:
    conjunto = generar_conjunto(GENERADOR)
    ultima = conjunto.transacciones[-1]["_id"]

    async with _d1_corriendo(base_efectividad) as supervisor:
        resumen = await ejecutar(RepoGenerador(base_efectividad), GENERADOR, limpiar=False)

        async def termino() -> bool:
            estado = await base_efectividad["deteccion_estado"].find_one({"_id": NOMBRE_D1})
            return estado is not None and estado.get("ultima_transaccion_id") == ultima

        await _esperar(termino, f"que D1 procese hasta {ultima}")
        fila = supervisor.informe().tarea(NOMBRE_D1)

    assert fila.ultimo_error is None, fila.ultimo_error
    assert await base_efectividad["deteccion_estado"].count_documents(
        {"clase": "descarte"}
    ) == 0, "ningun evento puede quedar en el descarte"

    filas, alertas_totales = await RepoEfectividad(base_efectividad).leer_filas_etiquetadas()
    reporte = calcular_efectividad(filas, alertas_totales=alertas_totales)
    print("\n" + formatear_tabla(reporte))

    assert reporte.transacciones_etiquetadas == 5000
    assert reporte.fraudes == resumen.total_fraude == conjunto.total_fraude
    assert reporte.detectados + reporte.perdidos == reporte.fraudes
    assert reporte.legitimas == 5000 - reporte.fraudes
    assert reporte.falsos_positivos + reporte.verdaderos_negativos == reporte.legitimas
    # Todas las transacciones de la base vienen del generador: toda alerta es TP o FP.
    assert reporte.alertas_sin_etiqueta == 0
    assert reporte.detectados + reporte.falsos_positivos == alertas_totales
    por_patron = {fila.patron: fila for fila in reporte.por_patron}
    assert {patron: fila.fraudes for patron, fila in por_patron.items()} == (
        conjunto.fraude_por_patron
    )
    assert sum(fila.detectados for fila in reporte.por_patron) == reporte.detectados
    assert reporte.detectados > 0, "con el catalogo real el motor tiene que atrapar algo"

    # La API devuelve el mismo reporte. El lifespan arranca su propio D1 sobre esta
    # base: reanuda desde el token de arriba y no tiene nada nuevo que procesar.
    os.environ["CENTINELA_MONGO__BASE"] = BASE_EFECTIVIDAD
    obtener_ajustes.cache_clear()
    try:
        aplicacion = crear_aplicacion()
        async with LifespanManager(aplicacion) as gestionada, AsyncClient(
            transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
        ) as http:
            respuesta = await http.get("/api/v1/efectividad")
    finally:
        os.environ.pop("CENTINELA_MONGO__BASE", None)
        obtener_ajustes.cache_clear()

    assert respuesta.status_code == 200
    assert respuesta.json() == reporte.model_dump(mode="json")
