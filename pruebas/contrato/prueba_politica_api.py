"""Contrato de `/api/v1/politica` (T-20, D-16), con el repositorio sustituido por un doble.

Corre sin Mongo. Que el validador real acepte la version nueva y que D1 la lea de la
base lo verifica `pruebas/integracion/prueba_politica_mongo.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient

from app.api.dependencias import dep_repo_reglas
from app.main import crear_aplicacion
from pruebas.dobles.catalogo_reglas import (
    cuerpo_simulacion_txn_001,
    politica_como_documento,
    reglas_como_documentos,
)
from pruebas.dobles.repos_falsos import RepoReglasFalso

RUTA = "/api/v1/politica"

CORTES_NUEVOS = [
    {"severidad": "MEDIA", "puntaje_minimo": 40, "puntaje_maximo": 59},
    {"severidad": "ALTA", "puntaje_minimo": 60, "puntaje_maximo": 79},
    {"severidad": "CRITICA", "puntaje_minimo": 80, "puntaje_maximo": 100},
]


@pytest.fixture
def repo_reglas() -> RepoReglasFalso:
    return RepoReglasFalso(reglas_como_documentos(), politica_como_documento())


@pytest.fixture
def aplicacion(repo_reglas: RepoReglasFalso) -> FastAPI:
    creada = crear_aplicacion()
    creada.dependency_overrides[dep_repo_reglas] = lambda: repo_reglas
    return creada


def error(respuesta: Any, estado: int, codigo: str) -> dict[str, Any]:
    assert respuesta.status_code == estado, respuesta.text
    cuerpo = respuesta.json()
    assert set(cuerpo) == {"error"}
    assert set(cuerpo["error"]) == {"codigo", "mensaje", "detalles"}
    assert cuerpo["error"]["codigo"] == codigo
    return cuerpo["error"]


@pytest.mark.asyncio
async def prueba_get_devuelve_la_politica_vigente(cliente: AsyncClient) -> None:
    respuesta = await cliente.get(RUTA)

    assert respuesta.status_code == status.HTTP_200_OK, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["_id"] == "POL-001"
    assert cuerpo["version"] == 1
    assert cuerpo["umbral_alerta"] == 40
    assert cuerpo["puntaje_maximo"] == 100
    assert [c["severidad"] for c in cuerpo["cortes_severidad"]] == ["MEDIA", "ALTA", "CRITICA"]
    assert cuerpo["versiones_anteriores"] == []


@pytest.mark.asyncio
async def prueba_sin_politica_es_404_con_la_forma_unica(
    repo_reglas: RepoReglasFalso, cliente: AsyncClient
) -> None:
    repo_reglas._politica = None
    error(await cliente.get(RUTA), 404, "no_encontrado")
    error(await cliente.patch(RUTA, json={"umbral_alerta": 45}), 404, "no_encontrado")


@pytest.mark.asyncio
async def prueba_patch_sube_la_version_y_guarda_la_anterior(
    repo_reglas: RepoReglasFalso, cliente: AsyncClient
) -> None:
    respuesta = await cliente.patch(RUTA, json={"cortes_severidad": CORTES_NUEVOS})

    assert respuesta.status_code == status.HTTP_200_OK, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["version"] == 2
    assert cuerpo["cortes_severidad"][1]["puntaje_maximo"] == 79
    [anterior] = cuerpo["versiones_anteriores"]
    assert anterior["version"] == 1
    assert anterior["cortes_severidad"][1]["puntaje_maximo"] == 69
    assert anterior["reemplazada_en"].endswith("Z") or "+00:00" in anterior["reemplazada_en"]
    assert repo_reglas.escrituras == 1


@pytest.mark.asyncio
async def prueba_el_cambio_rige_en_la_siguiente_evaluacion(cliente: AsyncClient) -> None:
    """Simular lee la politica vigente igual que D1: TXN-001 (75) pasa de CRITICA a ALTA."""
    antes = await cliente.post("/api/v1/reglas/simular", json=cuerpo_simulacion_txn_001())
    assert antes.json()["severidad"] == "CRITICA"
    assert antes.json()["politica_version"] == 1

    await cliente.patch(RUTA, json={"cortes_severidad": CORTES_NUEVOS})

    despues = await cliente.post("/api/v1/reglas/simular", json=cuerpo_simulacion_txn_001())
    assert despues.json()["severidad"] == "ALTA"
    assert despues.json()["politica_version"] == 2


@pytest.mark.asyncio
async def prueba_simular_con_politica_propuesta_sigue_funcionando(
    cliente: AsyncClient,
) -> None:
    propuesta = {
        "_id": "POL-001",
        "version": 9,
        "umbral_alerta": 40,
        "puntaje_maximo": 100,
        "cortes_severidad": CORTES_NUEVOS,
    }
    respuesta = await cliente.post(
        "/api/v1/reglas/simular", json={**cuerpo_simulacion_txn_001(), "politica": propuesta}
    )

    assert respuesta.status_code == status.HTTP_200_OK, respuesta.text
    assert respuesta.json()["severidad"] == "ALTA"
    assert respuesta.json()["politica_version"] == 9


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cortes", "fragmento"),
    [
        (  # hueco: 55 queda sin severidad
            [
                {"severidad": "MEDIA", "puntaje_minimo": 40, "puntaje_maximo": 54},
                {"severidad": "ALTA", "puntaje_minimo": 56, "puntaje_maximo": 69},
                {"severidad": "CRITICA", "puntaje_minimo": 70, "puntaje_maximo": 100},
            ],
            "hueco",
        ),
        (  # solape: 60 a 69 pertenece a dos
            [
                {"severidad": "MEDIA", "puntaje_minimo": 40, "puntaje_maximo": 69},
                {"severidad": "ALTA", "puntaje_minimo": 60, "puntaje_maximo": 79},
                {"severidad": "CRITICA", "puntaje_minimo": 70, "puntaje_maximo": 100},
            ],
            "solape",
        ),
        (  # no cubre hasta el puntaje maximo
            [
                {"severidad": "MEDIA", "puntaje_minimo": 40, "puntaje_maximo": 54},
                {"severidad": "ALTA", "puntaje_minimo": 55, "puntaje_maximo": 90},
            ],
            "puntaje_maximo",
        ),
        (  # no arranca en el umbral
            [
                {"severidad": "MEDIA", "puntaje_minimo": 50, "puntaje_maximo": 69},
                {"severidad": "CRITICA", "puntaje_minimo": 70, "puntaje_maximo": 100},
            ],
            "empezar en 40",
        ),
    ],
)
async def prueba_cortes_con_hueco_solape_o_sin_cobertura_dan_422(
    repo_reglas: RepoReglasFalso,
    cliente: AsyncClient,
    cortes: list[dict[str, Any]],
    fragmento: str,
) -> None:
    respuesta = await cliente.patch(RUTA, json={"cortes_severidad": cortes})

    cuerpo = error(respuesta, 422, "parametros_invalidos")
    assert any(fragmento in d["mensaje"] for d in cuerpo["detalles"]), cuerpo
    assert repo_reglas.escrituras == 0


@pytest.mark.asyncio
async def prueba_cambiar_el_umbral_sin_ajustar_los_cortes_da_422(
    cliente: AsyncClient,
) -> None:
    """Los cortes se validan contra el resultado final, no contra lo que vino."""
    error(await cliente.patch(RUTA, json={"umbral_alerta": 50}), 422, "parametros_invalidos")
    error(await cliente.patch(RUTA, json={"puntaje_maximo": 120}), 422, "parametros_invalidos")


@pytest.mark.asyncio
async def prueba_una_severidad_invalida_o_un_campo_ajeno_da_422(
    cliente: AsyncClient,
) -> None:
    baja = [{"severidad": "BAJA", "puntaje_minimo": 40, "puntaje_maximo": 100}]
    error(await cliente.patch(RUTA, json={"cortes_severidad": baja}), 422, "parametros_invalidos")
    error(await cliente.patch(RUTA, json={"vigente": False}), 422, "parametros_invalidos")
    error(await cliente.patch(RUTA, json={}), 422, "parametros_invalidos")


@pytest.mark.asyncio
async def prueba_version_esperada_vieja_da_409_sin_escribir(
    repo_reglas: RepoReglasFalso, cliente: AsyncClient
) -> None:
    respuesta = await cliente.patch(
        RUTA, json={"cortes_severidad": CORTES_NUEVOS, "version_esperada": 7}
    )

    error(respuesta, 409, "conflicto")
    assert repo_reglas.escrituras == 0


@pytest.mark.asyncio
async def prueba_otro_supervisor_en_el_medio_da_409(
    repo_reglas: RepoReglasFalso, cliente: AsyncClient
) -> None:
    repo_reglas.interferir_antes_de_editar = True

    respuesta = await cliente.patch(RUTA, json={"cortes_severidad": CORTES_NUEVOS})

    cuerpo = error(respuesta, 409, "conflicto")
    assert "version actual: 2" in cuerpo["detalles"][0]["mensaje"]
    assert repo_reglas.escrituras == 0


@pytest.mark.asyncio
async def prueba_un_cambio_que_no_cambia_nada_no_versiona(
    repo_reglas: RepoReglasFalso, cliente: AsyncClient
) -> None:
    respuesta = await cliente.patch(RUTA, json={"umbral_alerta": 40})

    assert respuesta.status_code == status.HTTP_200_OK
    assert respuesta.json()["version"] == 1
    assert repo_reglas.escrituras == 0


@pytest.mark.asyncio
async def prueba_los_cortes_desordenados_se_guardan_en_orden(cliente: AsyncClient) -> None:
    respuesta = await cliente.patch(
        RUTA, json={"cortes_severidad": list(reversed(CORTES_NUEVOS))}
    )

    assert respuesta.status_code == status.HTTP_200_OK, respuesta.text
    assert [c["severidad"] for c in respuesta.json()["cortes_severidad"]] == [
        "MEDIA",
        "ALTA",
        "CRITICA",
    ]


@pytest.mark.asyncio
async def prueba_openapi_publica_la_politica_con_la_forma_unica(cliente: AsyncClient) -> None:
    esquema = (await cliente.get("/openapi.json")).json()
    ruta = esquema["paths"][RUTA]

    assert set(ruta) == {"get", "patch"}
    for codigo in ("404", "409", "422"):
        referencia = ruta["patch"]["responses"][codigo]["content"]["application/json"]["schema"]
        assert referencia["$ref"].endswith("/RespuestaError")
