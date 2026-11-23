"""T-51 y D-41 contra MongoDB de verdad: aprobar, dar de baja y que R-07 lo note.

Se levanta la aplicacion completa (su lifespan lanza D1) sobre una base propia,
`antifraude_pruebas_listas_riesgo`, con los validadores reales copiados de la base sembrada
de pruebas, el indice unico de `alertas.transaccion_id` y el indice unico de
`listas_riesgo.cuenta` de `02-indices.js` (una cuenta, a lo sumo una entrada).

La prueba de punta a punta es la que importa: una cuenta propuesta por la red se aprueba
por la API, y la SIGUIENTE transaccion hacia ella dispara R-07 sin reiniciar nada; la que
fue antes de aprobar no, y despues de dar de baja deja de dispararse.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.deteccion.d1 import NOMBRE_D1
from app.main import crear_aplicacion
from app.modelos import Transaccion
from app.nucleo.config import obtener_ajustes
from app.repos.carga_demo import a_entero_64, cargar_demo
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.listas_riesgo import RepoListasRiesgo
from pruebas.conftest import BASE_SEMILLA, saltar_o_fallar
from pruebas.dobles.datos_demo import DATOS_DEMO
from pruebas.sesiones import encabezados_de

pytestmark = pytest.mark.mongo

BASE_LISTAS = "antifraude_pruebas_listas_riesgo"
CON_VALIDADOR = ("transacciones", "alertas", "deteccion_estado", "listas_riesgo")
DE_IDENTIDAD = ("clientes", "cuentas", "perfiles_comportamiento", "listas_riesgo")
CATALOGO = ("reglas_deteccion", "politica_deteccion")
LIMITE_SEGUNDOS = 20
DESTINO = "7001-0099"
APROBAR = f"/api/v1/red-mulas/8712-4455/propuestas/{DESTINO}"


@pytest_asyncio.fixture
async def base_listas(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    semilla = cliente[BASE_SEMILLA]
    validadores = await obtener_validadores(semilla)
    faltan = [nombre for nombre in CON_VALIDADOR if nombre not in validadores]
    if faltan:
        saltar_o_fallar(f"sin validador en {BASE_SEMILLA} para {faltan}")

    await cliente.drop_database(BASE_LISTAS)
    base = cliente[BASE_LISTAS]
    for nombre in CON_VALIDADOR:
        await recrear_con_validador(base, nombre, validadores[nombre])
    await base["alertas"].create_index(
        "transaccion_id", unique=True, name="idx_alertas_transaccion_id"
    )
    await base["listas_riesgo"].create_index(
        "cuenta", unique=True, name="idx_listas_riesgo_cuenta"
    )
    await cargar_demo(base, {nombre: DATOS_DEMO[nombre] for nombre in DE_IDENTIDAD})
    for nombre in CATALOGO:
        documentos = await semilla[nombre].find({}).to_list(length=None)
        if not documentos:
            saltar_o_fallar(f"{BASE_SEMILLA}.{nombre} vacia; correr 03-catalogos.js")
        await base[nombre].insert_many(documentos)
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_LISTAS)


async def _esperar(condicion: Callable[[], Awaitable[bool]], que: str) -> None:
    limite = asyncio.get_running_loop().time() + LIMITE_SEGUNDOS
    while not await condicion():
        if asyncio.get_running_loop().time() > limite:
            raise AssertionError(f"pasaron {LIMITE_SEGUNDOS} s esperando {que}")
        await asyncio.sleep(0.05)


@pytest_asyncio.fixture
async def http(base_listas: Any) -> AsyncIterator[AsyncClient]:
    """La app real sobre la base de la prueba, con D1 ya mirando el change stream."""
    os.environ["CENTINELA_MONGO__BASE"] = BASE_LISTAS
    obtener_ajustes.cache_clear()
    try:
        aplicacion = crear_aplicacion()
        async with LifespanManager(aplicacion) as gestionada, AsyncClient(
            headers=encabezados_de("supervisor", "AGT-002"),
            transport=ASGITransport(app=gestionada.app),
            base_url="http://pruebas",
        ) as cliente:
            supervisor = aplicacion.state.supervisor

            async def abierto() -> bool:
                return supervisor.informe().tarea(NOMBRE_D1).eventos_procesados >= 1

            await _esperar(abierto, "el primer latido de D1")
            yield cliente
    finally:
        os.environ.pop("CENTINELA_MONGO__BASE", None)
        obtener_ajustes.cache_clear()


def _transaccion(transaccion_id: str, destino: str, minutos: int) -> Transaccion:
    """Chica y a un destino nuevo: sola no suma el umbral con R-01; R-07 si lo alcanza."""
    return Transaccion(
        _id=transaccion_id,
        cuenta_origen="CTA-001",
        cuenta_destino=destino,
        monto_crc=20_000,
        fecha=datetime.now(UTC) + timedelta(minutes=minutos),
    )


async def _alerta_de(base: Any, transaccion_id: str) -> dict[str, Any] | None:
    async def llego() -> bool:
        estado = await base["deteccion_estado"].find_one({"_id": NOMBRE_D1})
        return estado is not None and estado.get("ultima_transaccion_id") == transaccion_id

    await _esperar(llego, f"que D1 procese {transaccion_id}")
    return await base["alertas"].find_one({"transaccion_id": transaccion_id})


async def _codigos(base: Any, transaccion_id: str) -> list[str]:
    alerta = await _alerta_de(base, transaccion_id)
    return [] if alerta is None else [r["codigo"] for r in alerta["reglas_disparadas"]]


# ---------------------------------------------------------------------------
# Aprobar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_aprobar_crea_la_entrada_vigente_con_la_forma_del_validador(
    http: AsyncClient, base_listas: Any
) -> None:
    antes = datetime.now(UTC)
    respuesta = await http.post(APROBAR)

    assert respuesta.status_code == 201
    entrada = await base_listas["listas_riesgo"].find_one({"cuenta": DESTINO})
    assert entrada is not None  # el validador real la acepto al insertar
    assert set(entrada) == {"_id", "cuenta", "motivo", "fecha_reporte", "vigente", "esquema_version"}
    assert entrada["_id"] == "RSK-005"  # el demo trae RSK-001..004
    assert entrada["vigente"] is True and entrada["esquema_version"] == 1
    assert entrada["motivo"] == "red de mulas desde 8712-4455"
    assert entrada["fecha_reporte"].replace(tzinfo=UTC) >= antes.replace(microsecond=0)
    assert respuesta.json()["entrada"]["_id"] == "RSK-005"


@pytest.mark.asyncio
async def prueba_aprobar_dos_veces_deja_una_sola_entrada(
    http: AsyncClient, base_listas: Any
) -> None:
    primera = await http.post(APROBAR)
    segunda = await http.post(APROBAR)
    en_paralelo = await asyncio.gather(http.post(APROBAR), http.post(APROBAR))

    assert (primera.status_code, segunda.status_code) == (201, 200)
    assert all(r.status_code == 200 for r in en_paralelo)
    assert await base_listas["listas_riesgo"].count_documents({"cuenta": DESTINO}) == 1


@pytest.mark.asyncio
async def prueba_aprobar_una_cuenta_con_entrada_no_vigente_la_reactiva_sin_duplicar(
    http: AsyncClient, base_listas: Any
) -> None:
    await base_listas["listas_riesgo"].update_one({"_id": "RSK-001"}, {"$set": {"vigente": False}})
    respuesta = await http.post("/api/v1/red-mulas/8712-4455/propuestas/6033-9001")

    assert respuesta.status_code == 201 and respuesta.json()["entrada"]["_id"] == "RSK-001"
    entradas = await base_listas["listas_riesgo"].find({"cuenta": "6033-9001"}).to_list(None)
    assert [(e["_id"], e["vigente"]) for e in entradas] == [("RSK-001", True)]
    assert entradas[0]["motivo"] == "red de mulas desde 8712-4455"


@pytest.mark.asyncio
async def prueba_el_repo_asigna_ids_sin_chocar_con_los_del_generador(base_listas: Any) -> None:
    # El generador usa cuatro cifras (RSK-0001) y las borra al regenerar: no se toca ese rango.
    await base_listas["listas_riesgo"].insert_one(
        {
            "_id": "RSK-0001",
            "cuenta": "7000-0001",
            "motivo": "generado",
            "fecha_reporte": datetime.now(UTC),
            "vigente": True,
            "esquema_version": 1,
        }
    )
    resultado = await RepoListasRiesgo(base_listas).aprobar(
        DESTINO, cuenta_partida="8712-4455", agente_id="AGT-002"
    )

    assert resultado.entrada["_id"] == "RSK-005" and resultado.creada


# ---------------------------------------------------------------------------
# Dar de baja
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_dar_de_baja_elimina_el_documento_y_deja_rastro(
    http: AsyncClient, base_listas: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="centinela.auditoria")
    await http.post(APROBAR)

    respuesta = await http.delete("/api/v1/listas-riesgo/RSK-005")
    otra_vez = await http.delete("/api/v1/listas-riesgo/RSK-005")

    assert respuesta.status_code == 204
    assert await base_listas["listas_riesgo"].count_documents({"_id": "RSK-005"}) == 0
    assert otra_vez.status_code == 404
    assert otra_vez.json()["error"]["codigo"] == "no_encontrado"
    baja = [r for r in caplog.records if "lista_riesgo_baja" in r.getMessage()]
    assert len(baja) == 1
    texto = baja[0].getMessage()
    assert f"cuenta={DESTINO}" in texto and "id=RSK-005" in texto and "dio_de_baja=AGT-002" in texto
    assert "red de mulas desde 8712-4455" in texto


@pytest.mark.asyncio
async def prueba_no_se_crea_ninguna_coleccion_para_el_rastro(
    http: AsyncClient, base_listas: Any
) -> None:
    antes = set(await base_listas.list_collection_names())
    await http.post(APROBAR)
    await http.delete("/api/v1/listas-riesgo/RSK-005")

    assert set(await base_listas.list_collection_names()) - antes <= {"indicadores_diarios"}


@pytest.mark.asyncio
async def prueba_el_agente_recibe_403_y_la_base_queda_intacta(
    http: AsyncClient, base_listas: Any
) -> None:
    agente = encabezados_de("agente", "AGT-001")
    aprobar = await http.post(APROBAR, headers=agente)
    borrar = await http.delete("/api/v1/listas-riesgo/RSK-001", headers=agente)

    assert aprobar.status_code == borrar.status_code == 403
    assert await base_listas["listas_riesgo"].count_documents({"cuenta": DESTINO}) == 0
    assert await base_listas["listas_riesgo"].count_documents({"_id": "RSK-001"}) == 1


# ---------------------------------------------------------------------------
# De punta a punta: R-07
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_r07_dispara_en_la_siguiente_transaccion_al_destino_aprobado(
    http: AsyncClient, base_listas: Any
) -> None:
    async def insertar(transaccion: Transaccion) -> None:
        await base_listas["transacciones"].insert_one(a_entero_64(transaccion.a_documento()))

    # 1. Antes de aprobar el destino no esta en la lista: R-07 no dispara.
    await insertar(_transaccion("TXN-031", DESTINO, 0))
    assert "R-07" not in await _codigos(base_listas, "TXN-031")

    # 2. Se aprueba y la siguiente transaccion (fecha posterior a la aprobacion) dispara R-07.
    assert (await http.post(APROBAR)).status_code == 201
    await insertar(_transaccion("TXN-032", DESTINO, 2))
    assert "R-07" in await _codigos(base_listas, "TXN-032")

    # 3. Se da de baja y la que sigue ya no dispara.
    assert (await http.delete("/api/v1/listas-riesgo/RSK-005")).status_code == 204
    await insertar(_transaccion("TXN-033", DESTINO, 4))
    assert "R-07" not in await _codigos(base_listas, "TXN-033")
