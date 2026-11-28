"""T-57 · Throughput de D1 ante una rafaga de 10.000 transacciones.

Marcada `volumen` ademas de `mongo`. Con D1 corriendo (como en el lifespan), el
generador inserta 10.000 transacciones de golpe (`insert_many` sin orden, sin ritmo) y la
prueba mide:

- transacciones evaluadas por segundo (10.000 sobre el tiempo hasta que D1 las termino);
- el rezago maximo que se vio, y el tiempo de puesta al dia: desde que termino la
  insercion hasta rezago 0.

El piso es holgado a proposito: detecta una regresion de orden de magnitud (por ejemplo,
que D1 pase de unas decenas de transacciones por segundo a menos de una), no el ruido de
la maquina. Las cifras medidas, con la maquina anotada, estan en docs/06-plataforma.md.
Se imprimen siempre; para verlas con el resto de la salida: `pytest -s`.

Base propia (`antifraude_pruebas_rezago_volumen`, T-34): validadores e indices reales de
la semilla de pruebas, y el catalogo real de reglas y politica.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.deteccion.d1 import NOMBRE_D1, d1
from app.generador.ejecucion import ejecutar
from app.nucleo.config import AjustesDeteccion, AjustesGenerador
from app.repos.deteccion import RepoDeteccion
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.generador import RepoGenerador
from app.supervision import Supervisor
from pruebas.conftest import BASE_SEMILLA, saltar_o_fallar

pytestmark = [pytest.mark.mongo, pytest.mark.volumen]

BASE_VOLUMEN = "antifraude_pruebas_rezago_volumen"
TRANSACCIONES = 10_000
CATALOGO = ("reglas_deteccion", "politica_deteccion")

# Piso: 25 transacciones por segundo son 400 s para las 10.000. D1 evalua del orden de
# cien por segundo en una maquina de desarrollo; bajar de 25 es una regresion seria.
PISO_EVALUADAS_POR_SEGUNDO = 25
LIMITE_SEGUNDOS = TRANSACCIONES / PISO_EVALUADAS_POR_SEGUNDO

AJUSTES_D1 = AjustesDeteccion(
    espera_cursor_ms=100,
    reintentos=1,
    espera_inicial_segundos=0.05,
    espera_maxima_segundos=0.2,
    rezago_intervalo_segundos=1,
)


@pytest_asyncio.fixture
async def base_volumen(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    trabajo = cliente[BASE_SEMILLA]
    validadores = await obtener_validadores(trabajo)
    if "transacciones" not in validadores or "deteccion_estado" not in validadores:
        saltar_o_fallar(f"{BASE_SEMILLA} no tiene los validadores de la semilla")

    await cliente.drop_database(BASE_VOLUMEN)
    base = cliente[BASE_VOLUMEN]
    for coleccion, validador in validadores.items():
        await recrear_con_validador(base, coleccion, validador)
    for coleccion in await trabajo.list_collection_names():
        for indice in await (await trabajo[coleccion].list_indexes()).to_list():
            if indice["name"] == "_id_":
                continue
            await base[coleccion].create_index(
                list(indice["key"].items()),
                name=indice["name"],
                unique=indice.get("unique", False),
                # T-82: los indices parciales (idx_alertas_no_fui_yo) comparten clave con otro.
                **(
                    {"partialFilterExpression": indice["partialFilterExpression"]}
                    if "partialFilterExpression" in indice
                    else {}
                ),
            )
    for coleccion in CATALOGO:
        documentos = await trabajo[coleccion].find({}).to_list(length=None)
        if not documentos:
            saltar_o_fallar(f"{BASE_SEMILLA}.{coleccion} vacia")
        await base[coleccion].insert_many(documentos)
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_VOLUMEN)


@pytest.mark.asyncio
async def prueba_diez_mil_transacciones_con_d1_corriendo(
    base_volumen: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    supervisor = Supervisor(tolerancia_segundos=30)
    repo = RepoDeteccion(base_volumen)
    supervisor.lanzar(NOMBRE_D1, d1(repo, supervisor, ajustes=AJUSTES_D1))
    try:
        # D1 arranca "desde ahora": se espera a que el change stream este abierto antes
        # de insertar, o las primeras transacciones quedarian antes del token.
        limite = time.monotonic() + 20
        while supervisor.informe().tarea(NOMBRE_D1).eventos_procesados < 1:
            assert time.monotonic() < limite, "D1 no abrio el change stream"
            await asyncio.sleep(0.05)

        inicio = time.monotonic()
        resumen = await ejecutar(
            RepoGenerador(base_volumen),
            AjustesGenerador(clientes=500, transacciones=TRANSACCIONES),
            limpiar=False,
        )
        fin_insercion = time.monotonic()
        ultima = f"TXN-{TRANSACCIONES:06d}"

        rezago_maximo = 0
        while True:
            medido = await repo.medir_rezago(NOMBRE_D1)
            rezago_maximo = max(rezago_maximo, medido.pendientes)
            estado = await base_volumen["deteccion_estado"].find_one({"_id": NOMBRE_D1})
            if (
                estado is not None
                and estado.get("ultima_transaccion_id") == ultima
                and medido.pendientes == 0
            ):
                break
            transcurrido = time.monotonic() - inicio
            assert transcurrido < LIMITE_SEGUNDOS, (
                f"D1 no se puso al dia en {LIMITE_SEGUNDOS:.0f} s "
                f"(rezago {medido.pendientes}, piso {PISO_EVALUADAS_POR_SEGUNDO}/s)"
            )
            await asyncio.sleep(0.25)
        fin = time.monotonic()
        informe = supervisor.informe().tarea(NOMBRE_D1)
    finally:
        await supervisor.detener()

    total = fin - inicio
    por_segundo = TRANSACCIONES / total
    alertas = await base_volumen["alertas"].count_documents({})
    descartes = await base_volumen["deteccion_estado"].count_documents({"clase": "descarte"})
    with capsys.disabled():
        print(
            f"\n[volumen] {TRANSACCIONES} transacciones: insercion {resumen.segundos:.1f} s "
            f"({resumen.documentos_por_segundo:.0f} docs/s); D1 evalua {por_segundo:.0f} tx/s "
            f"({total:.1f} s en total); puesta al dia {fin - fin_insercion:.1f} s tras la "
            f"insercion; rezago maximo visto {rezago_maximo}; alertas {alertas}; "
            f"latencia p50 {informe.latencia.p50} ms, p95 {informe.latencia.p95} ms"
        )
    assert descartes == 0, "D1 descarto eventos durante la rafaga"
    assert alertas > 0
    assert await base_volumen["transacciones"].count_documents({}) == TRANSACCIONES
    assert por_segundo > PISO_EVALUADAS_POR_SEGUNDO
