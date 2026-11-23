"""H-15 contra MongoDB de verdad: filtros sobre datos generados y el plan de ejecucion.

Base propia (`antifraude_pruebas_busqueda`), con los validadores reales copiados de
`antifraude` y los MISMOS indices de `transacciones` y `alertas` que la base de trabajo
(se leen de ahi, asi la prueba no se desincroniza de `02-indices.js`). Las alertas se
siembran directo, sin D1: una de cada estado repartida sobre las transacciones de fraude.

Compara dos caminos sobre los mismos datos: el repositorio contra Mongo y el doble en
memoria. Si se separan, esta prueba lo dice.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.generador.ejecucion import ejecutar
from app.main import crear_aplicacion
from app.modelos import EstadoAlerta
from app.nucleo.config import AjustesGenerador, obtener_ajustes
from app.repos.busqueda import FiltroBusqueda, RepoBusqueda
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.generador import RepoGenerador
from pruebas.conftest import BASE_SEMILLA, _asegurar_semilla, saltar_o_fallar
from pruebas.dobles.repo_busqueda_falso import RepoBusquedaFalso
from pruebas.sesiones import encabezados_de

pytestmark = pytest.mark.mongo

BASE_BUSQUEDA = "antifraude_pruebas_busqueda"
COLECCIONES = ("clientes", "cuentas", "perfiles_comportamiento", "transacciones")
ESTADOS = [e.value for e in EstadoAlerta]


async def preparar_base(cliente: Any, nombre: str, ajustes: AjustesGenerador) -> Any:
    """Base con validadores e indices reales, datos generados y alertas sembradas."""
    # Validadores e indices de la base sembrada de pruebas (01, 02 y 03 de la rama);
    # T-34: ninguna prueba lee `antifraude`.
    await _asegurar_semilla(obtener_ajustes().mongo.uri)
    trabajo = cliente[BASE_SEMILLA]
    validadores = await obtener_validadores(trabajo)
    faltan = [c for c in COLECCIONES if c not in validadores]
    if faltan:
        saltar_o_fallar(f"sin validador en {BASE_SEMILLA} para {faltan}")

    await cliente.drop_database(nombre)
    base = cliente[nombre]
    for coleccion in COLECCIONES:
        await recrear_con_validador(base, coleccion, validadores[coleccion])
    for coleccion in ("transacciones", "alertas"):
        for indice in await (await trabajo[coleccion].list_indexes()).to_list():
            if indice["name"] == "_id_":
                continue
            await base[coleccion].create_index(
                list(indice["key"].items()),
                name=indice["name"],
                unique=indice.get("unique", False),
            )

    await ejecutar(RepoGenerador(base), ajustes, limpiar=False)

    # Alertas sobre las transacciones de fraude, repartiendo los estados.
    fraude = await base.transacciones.find(
        {"simulacion.fraude": True}, {"_id": 1, "fecha": 1, "monto_crc": 1}
    ).sort("_id", 1).to_list(None)
    alertas = [
        {
            "_id": f"ALR-{numero:05d}",
            "transaccion_id": t["_id"],
            "estado": ESTADOS[numero % len(ESTADOS)],
            "fecha_creacion": t["fecha"],
            "esquema_version": 1,
        }
        for numero, t in enumerate(fraude, start=1)
    ]
    if alertas:
        await base.alertas.insert_many(alertas)
    return base


@pytest_asyncio.fixture
async def base_busqueda(ajustes_modulo: Any) -> AsyncIterator[Any]:
    from app.nucleo.db import ciclo_cliente

    async with ciclo_cliente(ajustes_modulo) as cliente:
        try:
            yield await preparar_base(
                cliente, BASE_BUSQUEDA, AjustesGenerador(clientes=200, transacciones=5000)
            )
        finally:
            await cliente.drop_database(BASE_BUSQUEDA)


@pytest.fixture
def ajustes_modulo() -> Any:
    from app.nucleo.config import Ajustes

    return Ajustes(mongo={"uri": Ajustes().mongo.uri, "base": BASE_BUSQUEDA})


@pytest_asyncio.fixture
async def mongo_ok(base_busqueda: Any, ajustes_modulo: Any) -> Any:
    # Reusa el sondeo del conftest: salta (o falla en modo estricto) sin Mongo.
    from pruebas.conftest import MENSAJE_SIN_MONGO, _mongo_responde

    if not await _mongo_responde(ajustes_modulo):
        saltar_o_fallar(MENSAJE_SIN_MONGO)
    return base_busqueda


FILTROS = {
    "sin_filtros": FiltroBusqueda(),
    "monto": FiltroBusqueda(monto_min=50_000, monto_max=400_000),
    "fecha": FiltroBusqueda(
        desde=datetime(2026, 9, 25, tzinfo=UTC), hasta=datetime(2026, 9, 27, tzinfo=UTC)
    ),
    "canal": FiltroBusqueda(canales=("sinpe_movil",)),
    "estado_confirmada": FiltroBusqueda(estado_alerta=EstadoAlerta.CONFIRMADA),
    "estado_y_monto": FiltroBusqueda(estado_alerta=EstadoAlerta.NUEVA, monto_min=100_000),
    "cuenta_cta": FiltroBusqueda(cuenta="CTA-001"),
    "cuenta_cta_y_fecha": FiltroBusqueda(
        cuenta="CTA-005", desde=datetime(2026, 9, 1, tzinfo=UTC)
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("nombre", sorted(FILTROS))
async def prueba_mongo_y_el_doble_devuelven_lo_mismo(mongo_ok: Any, nombre: str) -> None:
    base = mongo_ok
    transacciones = await base.transacciones.find({}).to_list(None)
    alertas = await base.alertas.find({}).to_list(None)
    doble = RepoBusquedaFalso(transacciones, alertas)
    real = RepoBusqueda(base)

    filtro = FILTROS[nombre]
    esperado = await doble.buscar(filtro, limite=20, desplazamiento=0)
    obtenido = await real.buscar(filtro, limite=20, desplazamiento=0)

    assert obtenido.total == esperado.total
    assert [d["_id"] for d in obtenido.documentos] == [d["_id"] for d in esperado.documentos]
    assert [d["alerta_estado"] for d in obtenido.documentos] == [
        d["alerta_estado"] for d in esperado.documentos
    ]
    assert all("simulacion" not in d for d in obtenido.documentos)


@pytest.mark.asyncio
async def prueba_la_cuenta_encuentra_origen_cta_origen_sinpe_y_destino(mongo_ok: Any) -> None:
    base = mongo_ok
    destino = await base.transacciones.find_one({"cuenta_destino": {"$regex": "^6"}})
    real = RepoBusqueda(base)

    por_cta = await real.buscar(FiltroBusqueda(cuenta=destino["cuenta_origen"]), limite=1000)
    por_sinpe = await real.buscar(FiltroBusqueda(cuenta=destino["sinpe_origen"]), limite=1000)
    ids_cta = {d["_id"] for d in por_cta.documentos}
    assert destino["_id"] in ids_cta and destino["_id"] in {d["_id"] for d in por_sinpe.documentos}

    como_destino = await real.buscar(FiltroBusqueda(cuenta=destino["cuenta_destino"]), limite=1000)
    assert destino["_id"] in {d["_id"] for d in como_destino.documentos}
    assert all(
        destino["cuenta_destino"]
        in (d["cuenta_origen"], d.get("sinpe_origen"), d["cuenta_destino"])
        for d in como_destino.documentos
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("nombre", "indice"),
    [
        ("cuenta_cta", "idx_transacciones_cuenta_origen_fecha"),
        ("monto", "idx_transacciones_monto_fecha"),
        ("estado_confirmada", "_id_"),
    ],
)
async def prueba_depurar_trae_ixscan_con_el_indice_esperado(
    mongo_ok: Any, nombre: str, indice: str
) -> None:
    resultado = await RepoBusqueda(mongo_ok).buscar(
        FILTROS[nombre], limite=20, depurar=True
    )
    assert resultado.plan is not None
    assert "IXSCAN" in resultado.plan["etapas"], resultado.plan["etapas"]
    assert indice in resultado.plan["indices"], resultado.plan["indices"]
    assert "COLLSCAN" not in resultado.plan["etapas"]
    assert resultado.plan["documentos_examinados"] is not None


@pytest.mark.asyncio
async def prueba_la_cuenta_usa_los_tres_indices_con_or(mongo_ok: Any) -> None:
    resultado = await RepoBusqueda(mongo_ok).buscar(
        FiltroBusqueda(cuenta="8712-4455"), limite=20, depurar=True
    )
    assert resultado.plan is not None
    assert "COLLSCAN" not in resultado.plan["etapas"], resultado.plan["etapas"]
    assert {
        "idx_transacciones_cuenta_origen_fecha",
        "idx_transacciones_sinpe_origen_fecha",
        "idx_transacciones_cuenta_destino",
    } <= set(resultado.plan["indices"])


@pytest_asyncio.fixture
async def cliente_http(mongo_ok: Any) -> AsyncIterator[AsyncClient]:
    os.environ["CENTINELA_MONGO__BASE"] = BASE_BUSQUEDA
    obtener_ajustes.cache_clear()
    try:
        async with LifespanManager(crear_aplicacion()) as gestionada, AsyncClient(
            headers=encabezados_de(),
            transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
        ) as http:
            yield http
    finally:
        os.environ.pop("CENTINELA_MONGO__BASE", None)
        obtener_ajustes.cache_clear()


@pytest.mark.asyncio
async def prueba_la_api_completa_contra_mongo(cliente_http: AsyncClient) -> None:
    cuerpo = (
        await cliente_http.get(
            "/api/v1/transacciones",
            params={"estado_alerta": "confirmada", "monto_min": 1, "depurar": "true", "limite": 5},
        )
    ).json()
    assert cuerpo["total"] > 0 and len(cuerpo["elementos"]) == 5
    assert {e["alerta_estado"] for e in cuerpo["elementos"]} == {"confirmada"}
    assert "IXSCAN" in cuerpo["plan"]["etapas"]
    assert all(isinstance(e["monto_crc"], int) for e in cuerpo["elementos"])


@pytest.mark.asyncio
async def prueba_la_pantalla_y_el_csv_contra_mongo(cliente_http: AsyncClient) -> None:
    pantalla = await cliente_http.get("/busqueda", params={"estado_alerta": "nueva", "limite": "10"})
    assert pantalla.status_code == 200 and "ALR-" in pantalla.text
    exportado = await cliente_http.get(
        "/busqueda/exportar.csv", params={"estado_alerta": "nueva"}
    )
    assert exportado.status_code == 200
    lineas = exportado.text.strip().split("\r\n")
    assert len(lineas) - 1 == int(exportado.headers["x-filas-exportadas"]) > 0
    assert all(",nueva" in linea for linea in lineas[1:])
