"""Contrato de `GET /api/v1/transacciones` (H-15) con el repositorio sustituido por un doble.

Corre sin Mongo. La consulta real, los indices y el plan se prueban en
`pruebas/integracion/prueba_busqueda_mongo.py`.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_repo_busqueda
from app.main import crear_aplicacion
from app.repos.busqueda import (
    FiltroBusqueda,
    ProtocoloRepoBusqueda,
    RepoBusqueda,
    construir_filtro_busqueda,
)
from pruebas.dobles.repo_busqueda_falso import RepoBusquedaFalso
from pruebas.sesiones import encabezados_de

RUTA = "/api/v1/transacciones"


def _txn(numero: int, **cambios: Any) -> dict[str, Any]:
    base = {
        "_id": f"TXN-{numero:03d}",
        "cuenta_origen": "CTA-001",
        "sinpe_origen": "8712-4455",
        "cuenta_destino": "6033-9001",
        "monto_crc": 10_000 * numero,
        "moneda": "CRC",
        "canal": "sinpe_movil",
        "fecha": datetime(2026, 10, numero, 12, 0, tzinfo=UTC),
        "simulacion": {"fraude": False},
        "esquema_version": 1,
    }
    return {**base, **cambios}


TRANSACCIONES = [
    _txn(1),
    _txn(2, cuenta_origen="CTA-002", sinpe_origen="8800-1111"),
    _txn(3, cuenta_destino="7000-0002"),
    _txn(4, cuenta_origen="CTA-002", sinpe_origen="8800-1111", cuenta_destino="8712-4455"),
    _txn(5),
    _txn(6),
]
ALERTAS = [
    {"_id": "ALR-001", "transaccion_id": "TXN-003", "estado": "nueva"},
    {"_id": "ALR-002", "transaccion_id": "TXN-005", "estado": "confirmada"},
    {"_id": "ALR-003", "transaccion_id": "TXN-006", "estado": "nueva"},
]


@pytest.fixture
def repo() -> RepoBusquedaFalso:
    return RepoBusquedaFalso(TRANSACCIONES, ALERTAS)


@pytest_asyncio.fixture
async def cliente(repo: RepoBusquedaFalso) -> AsyncIterator[AsyncClient]:
    aplicacion = crear_aplicacion()
    aplicacion.dependency_overrides[dep_repo_busqueda] = lambda: repo
    async with (
        LifespanManager(aplicacion) as gestionada,
        AsyncClient(headers=encabezados_de(), transport=ASGITransport(app=gestionada.app), base_url="http://pruebas") as http,
    ):
        yield http


async def _ids(cliente: AsyncClient, **parametros: Any) -> list[str]:
    respuesta = await cliente.get(RUTA, params=parametros)
    assert respuesta.status_code == 200, respuesta.text
    return [e["transaccion_id"] for e in respuesta.json()["elementos"]]


def prueba_el_doble_cumple_el_protocolo() -> None:
    assert isinstance(RepoBusquedaFalso([], []), ProtocoloRepoBusqueda)
    protocolo = set(inspect.signature(ProtocoloRepoBusqueda.buscar).parameters)
    assert set(inspect.signature(RepoBusqueda.buscar).parameters) == protocolo
    assert set(inspect.signature(RepoBusquedaFalso.buscar).parameters) == protocolo


def prueba_el_filtro_de_mongo_usa_los_operadores_decididos() -> None:
    desde = datetime(2026, 10, 1, tzinfo=UTC)
    hasta = datetime(2026, 10, 9, tzinfo=UTC)
    filtro = construir_filtro_busqueda(
        FiltroBusqueda(
            desde=desde,
            hasta=hasta,
            monto_min=1000,
            monto_max=9000,
            cuenta="CTA-001",
            canales=("sinpe_movil",),
        ),
        ids_por_estado=["TXN-003"],
    )
    assert filtro["fecha"] == {"$gte": desde, "$lte": hasta}
    assert filtro["monto_crc"] == {"$gte": 1000, "$lte": 9000}
    assert filtro["canal"] == {"$in": ["sinpe_movil"]}
    assert filtro["_id"] == {"$in": ["TXN-003"]}
    assert {"cuenta_origen": "CTA-001"} in filtro["$or"]
    assert {"sinpe_origen": "CTA-001"} in filtro["$or"]
    assert {"cuenta_destino": "CTA-001"} in filtro["$or"]
    assert construir_filtro_busqueda(FiltroBusqueda()) == {}


@pytest.mark.asyncio
async def prueba_sin_filtros_devuelve_todo_de_mas_reciente_a_mas_antigua(
    cliente: AsyncClient,
) -> None:
    cuerpo = (await cliente.get(RUTA)).json()
    assert cuerpo["total"] == 6
    assert [e["transaccion_id"] for e in cuerpo["elementos"]] == [
        "TXN-006", "TXN-005", "TXN-004", "TXN-003", "TXN-002", "TXN-001",
    ]
    assert cuerpo["plan"] is None


@pytest.mark.asyncio
async def prueba_el_elemento_publica_su_forma_y_oculta_la_simulacion(
    cliente: AsyncClient,
) -> None:
    elemento = (await cliente.get(RUTA, params={"monto_min": 60000})).json()["elementos"][0]
    assert set(elemento) == {
        "transaccion_id", "cuenta_origen", "sinpe_origen", "cuenta_destino", "monto_crc",
        "moneda", "canal", "fecha", "alerta_id", "alerta_estado",
    }
    assert elemento["monto_crc"] == 60000 and isinstance(elemento["monto_crc"], int)
    assert elemento["alerta_id"] == "ALR-003" and elemento["alerta_estado"] == "nueva"
    assert elemento["fecha"].startswith("2026-10-06T12:00:00")


@pytest.mark.asyncio
async def prueba_rango_de_fecha_es_inclusivo(cliente: AsyncClient) -> None:
    ids = await _ids(cliente, desde="2026-10-02T12:00:00Z", hasta="2026-10-04T12:00:00Z")
    assert ids == ["TXN-004", "TXN-003", "TXN-002"]


@pytest.mark.asyncio
async def prueba_rango_de_monto_es_inclusivo(cliente: AsyncClient) -> None:
    assert await _ids(cliente, monto_min=20000, monto_max=40000) == [
        "TXN-004", "TXN-003", "TXN-002",
    ]
    assert await _ids(cliente, monto_min=60000) == ["TXN-006"]
    assert await _ids(cliente, monto_max=10000) == ["TXN-001"]


@pytest.mark.asyncio
async def prueba_cuenta_busca_origen_cta_origen_sinpe_y_destino(
    cliente: AsyncClient,
) -> None:
    assert await _ids(cliente, cuenta="CTA-002") == ["TXN-004", "TXN-002"]
    assert await _ids(cliente, cuenta="8800-1111") == ["TXN-004", "TXN-002"]
    # 8712-4455 es sinpe_origen de la mayoria y destino de TXN-004
    assert await _ids(cliente, cuenta="8712-4455") == [
        "TXN-006", "TXN-005", "TXN-004", "TXN-003", "TXN-001",
    ]
    assert await _ids(cliente, cuenta="7000-0002") == ["TXN-003"]


@pytest.mark.asyncio
async def prueba_canal_valida_el_vocabulario(cliente: AsyncClient) -> None:
    assert len(await _ids(cliente, canal="sinpe_movil")) == 6
    assert (await cliente.get(RUTA, params={"canal": "cheque"})).status_code == 422


@pytest.mark.asyncio
async def prueba_estado_de_alerta_filtra_y_cada_fila_trae_su_alerta(
    cliente: AsyncClient,
) -> None:
    assert await _ids(cliente, estado_alerta="nueva") == ["TXN-006", "TXN-003"]
    assert await _ids(cliente, estado_alerta="confirmada") == ["TXN-005"]
    assert await _ids(cliente, estado_alerta="escalada") == []
    assert (await cliente.get(RUTA, params={"estado_alerta": "rara"})).status_code == 422


@pytest.mark.asyncio
async def prueba_los_filtros_se_combinan_con_y(cliente: AsyncClient) -> None:
    assert await _ids(
        cliente, cuenta="8712-4455", monto_min=30000, estado_alerta="nueva"
    ) == ["TXN-006", "TXN-003"]
    assert await _ids(cliente, cuenta="CTA-002", monto_max=20000, canal="sinpe_movil") == [
        "TXN-002"
    ]
    assert await _ids(cliente, cuenta="CTA-002", estado_alerta="nueva") == []


@pytest.mark.asyncio
async def prueba_paginacion_acotada_y_con_total(
    cliente: AsyncClient, repo: RepoBusquedaFalso
) -> None:
    cuerpo = (await cliente.get(RUTA, params={"limite": 2, "desplazamiento": 2})).json()
    assert cuerpo["total"] == 6 and cuerpo["limite"] == 2 and cuerpo["desplazamiento"] == 2
    assert [e["transaccion_id"] for e in cuerpo["elementos"]] == ["TXN-004", "TXN-003"]

    acotado = (await cliente.get(RUTA, params={"limite": 100000})).json()
    assert acotado["limite"] == 200, "app.pagina_maxima de config/centinela.yml"
    assert repo.llamadas[-1]["limite"] == 200
    por_omision = (await cliente.get(RUTA)).json()
    assert por_omision["limite"] == 50, "app.pagina_por_omision"


@pytest.mark.asyncio
async def prueba_limite_y_desplazamiento_invalidos_son_422(cliente: AsyncClient) -> None:
    assert (await cliente.get(RUTA, params={"limite": 0})).status_code == 422
    assert (await cliente.get(RUTA, params={"desplazamiento": -1})).status_code == 422


@pytest.mark.asyncio
async def prueba_depurar_devuelve_el_plan(
    cliente: AsyncClient, repo: RepoBusquedaFalso
) -> None:
    cuerpo = (await cliente.get(RUTA, params={"depurar": "true"})).json()
    assert cuerpo["plan"]["etapas"] and repo.llamadas[-1]["depurar"] is True
    sin = (await cliente.get(RUTA, params={"depurar": "false"})).json()
    assert sin["plan"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parametros", "campo"),
    [
        ({"monto_min": "1500.5"}, "monto_min"),
        ({"monto_max": "10.5"}, "monto_max"),
        ({"monto_min": "abc"}, "monto_min"),
        ({"monto_min": "-1"}, "monto_min"),
        ({"monto_min": "5000", "monto_max": "1000"}, "monto_min"),
        ({"desde": "2026-10-05T00:00:00Z", "hasta": "2026-10-01T00:00:00Z"}, "desde"),
        ({"desde": "no-es-fecha"}, "desde"),
        ({"cuenta": "x'; drop"}, "cuenta"),
    ],
)
async def prueba_parametros_invalidos_son_422_con_la_forma_unica(
    cliente: AsyncClient, parametros: dict[str, str], campo: str
) -> None:
    respuesta = await cliente.get(RUTA, params=parametros)
    assert respuesta.status_code == 422
    error = respuesta.json()["error"]
    assert error["codigo"] == "parametros_invalidos"
    assert campo in [d["campo"] for d in error["detalles"]]


@pytest.mark.asyncio
async def prueba_una_fecha_sin_zona_se_toma_como_utc(cliente: AsyncClient) -> None:
    assert await _ids(cliente, desde="2026-10-06T12:00:00") == ["TXN-006"]


@pytest.mark.asyncio
async def prueba_el_csv_de_la_api_aplica_los_filtros_con_montos_enteros_y_hora_cr(
    cliente: AsyncClient,
) -> None:
    respuesta = await cliente.get(
        RUTA + "/exportar.csv",
        params={"cuenta": "CTA-002", "monto_min": 30000, "desde": "2026-10-01T00:00:00-06:00"},
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.headers["content-type"].startswith("text/csv")
    assert "attachment" in respuesta.headers["content-disposition"]
    assert respuesta.headers["x-filas-exportadas"] == "1"
    lineas = respuesta.text.strip().split("\r\n")
    assert lineas[0].startswith("transaccion_id,fecha_hora_cr,cuenta_origen")
    # 12:00 UTC del 4 de octubre son las 06:00 en Costa Rica; el monto va entero.
    assert lineas[1].startswith("TXN-004,2026-10-04 06:00:00,CTA-002,")
    assert ",40000,CRC," in lineas[1]


@pytest.mark.asyncio
async def prueba_el_csv_de_la_api_rechaza_un_rango_invertido_con_la_forma_unica(
    cliente: AsyncClient,
) -> None:
    respuesta = await cliente.get(RUTA + "/exportar.csv", params={"monto_min": 9, "monto_max": 1})
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["codigo"] == "parametros_invalidos"
