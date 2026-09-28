"""`GET /api/v1/alertas` contra MongoDB de verdad.

Necesita el replica set; se salta sola si no esta. Es la unica prueba que ejercita el
camino completo ruta -> dependencia -> repositorio -> MongoDB, sin ningun doble. Sirve
para lo que el doble no puede: comprobar que el filtro que `construir_filtro` arma de
verdad significa en Mongo lo que creemos que significa.
"""

from __future__ import annotations

import pytest
from fastapi import status
from httpx import AsyncClient

pytestmark = pytest.mark.mongo

RUTA = "/api/v1/alertas"


@pytest.mark.asyncio
async def prueba_devuelve_el_demo_cargado(cliente_con_mongo: AsyncClient) -> None:
    respuesta = await cliente_con_mongo.get(RUTA)

    assert respuesta.status_code == status.HTTP_200_OK
    cuerpo = respuesta.json()

    assert cuerpo["total"] == 3
    assert [alerta["_id"] for alerta in cuerpo["elementos"]] == [
        "ALR-001",
        "ALR-003",
        "ALR-002",
    ]


@pytest.mark.asyncio
async def prueba_el_dinero_vuelve_de_mongo_como_entero(
    cliente_con_mongo: AsyncClient,
) -> None:
    """Ida y vuelta del contrato de dinero: se escribe Int64 y se lee entero."""
    cuerpo = (await cliente_con_mongo.get(RUTA, params={"estado": "confirmada"})).json()
    alerta = cuerpo["elementos"][0]

    assert alerta["monto_crc"] == 750_000
    assert isinstance(alerta["monto_crc"], int)
    assert alerta["moneda"] == "CRC"


@pytest.mark.asyncio
async def prueba_la_fecha_vuelve_de_mongo_en_utc(cliente_con_mongo: AsyncClient) -> None:
    """`tz_aware=True` en el cliente es lo que evita que vuelva una fecha ingenua."""
    cuerpo = (await cliente_con_mongo.get(RUTA, params={"estado": "confirmada"})).json()

    fecha = cuerpo["elementos"][0]["fecha_creacion"]
    assert fecha.startswith("2026-09-25T04:47")
    assert fecha.endswith("Z") or "+00:00" in fecha


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filtro", "esperados"),
    [
        ({"estado": "nueva"}, ["ALR-002"]),
        ({"estado": "confirmada"}, ["ALR-001"]),
        ({"estado": "descartada"}, []),
        ({"severidad": "CRITICA"}, ["ALR-001"]),
        ({"severidad": "ALTA"}, ["ALR-003"]),
        ({"estado": "nueva", "severidad": "CRITICA"}, []),
    ],
)
async def prueba_los_filtros_funcionan_contra_mongo(
    cliente_con_mongo: AsyncClient, filtro: dict[str, str], esperados: list[str]
) -> None:
    cuerpo = (await cliente_con_mongo.get(RUTA, params=filtro)).json()

    assert [alerta["_id"] for alerta in cuerpo["elementos"]] == esperados
    assert cuerpo["total"] == len(esperados)


@pytest.mark.asyncio
async def prueba_el_rango_de_fecha_funciona_contra_mongo(
    cliente_con_mongo: AsyncClient,
) -> None:
    cuerpo = (
        await cliente_con_mongo.get(
            RUTA,
            params={
                "desde": "2026-09-24T17:00:00Z",
                "hasta": "2026-09-24T23:00:00Z",
            },
        )
    ).json()

    assert [alerta["_id"] for alerta in cuerpo["elementos"]] == ["ALR-003", "ALR-002"]


@pytest.mark.asyncio
async def prueba_la_paginacion_no_repite_contra_mongo(
    cliente_con_mongo: AsyncClient,
) -> None:
    """El desempate por `_id` del orden se prueba de verdad solo contra la base."""
    vistos: list[str] = []
    for desplazamiento in range(3):
        cuerpo = (
            await cliente_con_mongo.get(
                RUTA, params={"limite": 1, "desplazamiento": desplazamiento}
            )
        ).json()
        vistos.extend(alerta["_id"] for alerta in cuerpo["elementos"])
        assert cuerpo["total"] == 3

    assert len(set(vistos)) == 3


@pytest.mark.asyncio
async def prueba_el_limite_se_acota_tambien_contra_mongo(
    cliente_con_mongo: AsyncClient,
) -> None:
    cuerpo = (await cliente_con_mongo.get(RUTA, params={"limite": 100_000})).json()

    assert cuerpo["limite"] == 200


@pytest.mark.asyncio
async def prueba_salud_confirma_el_replica_set(cliente_con_mongo: AsyncClient) -> None:
    """`/salud` tiene que decir el nombre del conjunto, no solo "ok"."""
    cuerpo = (await cliente_con_mongo.get("/salud")).json()

    assert cuerpo["estado"] == "ok"
    assert cuerpo["mongo_conectado"] is True
    assert cuerpo["replica_set"] == "rsfraude"
