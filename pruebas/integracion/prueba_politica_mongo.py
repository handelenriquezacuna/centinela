"""T-20 · `/api/v1/politica` contra MongoDB de verdad, y T-24 en la API.

Sobre la base de pruebas, con el validador REAL de `politica_deteccion` copiado de
`antifraude` y la politica sembrada por 03-catalogos.js. Lo que sostiene:

- `PATCH` sube la version a 2, guarda la 1 en `versiones_anteriores` y el validador la
  acepta; un corte con hueco da 422 y no escribe;
- el cambio rige en la siguiente lectura de D1 (`RepoDeteccion.leer_politica_vigente`)
  y en la siguiente simulacion, sin reiniciar nada;
- dos ediciones con la misma version leida: la segunda no encuentra nada (409);
- con un validador viejo (sin la historia), el `PATCH` responde la forma unica con la
  sugerencia de `python tareas.py validadores`, no un 500.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi import status
from httpx import AsyncClient

from app.modelos import COLECCION_POLITICA
from app.repos.deteccion import RepoDeteccion
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.reglas import COLECCION_REGLAS, RepoReglas
from pruebas.conftest import BASE_SEMILLA, saltar_o_fallar
from pruebas.dobles.catalogo_reglas import cuerpo_simulacion_txn_001

pytestmark = pytest.mark.mongo

BASE_DE_TRABAJO = BASE_SEMILLA  # semilla propia de las pruebas, no `antifraude`
RUTA = "/api/v1/politica"

CORTES_NUEVOS = [
    {"severidad": "MEDIA", "puntaje_minimo": 35, "puntaje_maximo": 59},
    {"severidad": "ALTA", "puntaje_minimo": 60, "puntaje_maximo": 79},
    {"severidad": "CRITICA", "puntaje_minimo": 80, "puntaje_maximo": 100},
]


async def _sembrar(base_mongo: Any, validador: dict[str, Any]) -> None:
    trabajo = base_mongo.client[BASE_DE_TRABAJO]
    politicas = await trabajo[COLECCION_POLITICA].find().to_list(length=None)
    reglas = await trabajo[COLECCION_REGLAS].find().to_list(length=None)
    if not politicas or not reglas:
        saltar_o_fallar("antifraude sin catalogo; correr python tareas.py datos-demo")
    await recrear_con_validador(base_mongo, COLECCION_POLITICA, validador)
    await base_mongo[COLECCION_POLITICA].insert_many(politicas)
    await base_mongo[COLECCION_REGLAS].delete_many({})
    await base_mongo[COLECCION_REGLAS].insert_many(reglas)


async def _validador_real(base_mongo: Any) -> dict[str, Any]:
    validadores = await obtener_validadores(base_mongo.client[BASE_DE_TRABAJO])
    if COLECCION_POLITICA not in validadores:
        saltar_o_fallar("antifraude sin validador de politica; correr python tareas.py datos-demo")
    return validadores[COLECCION_POLITICA]


def _con_historia(validador: dict[str, Any]) -> dict[str, Any]:
    """El validador real, con `versiones_anteriores` aunque la base aun no lo declare.

    Las pruebas de comportamiento no deben depender de que la base local este al dia;
    esa dependencia la sostiene `prueba_el_validador_real_declara_la_historia`.
    """
    copia = copy.deepcopy(validador)
    copia["$jsonSchema"]["properties"].setdefault(
        "versiones_anteriores", {"bsonType": "array", "items": {"bsonType": "object"}}
    )
    return copia


def _sin_historia(validador: dict[str, Any]) -> dict[str, Any]:
    copia = copy.deepcopy(validador)
    copia["$jsonSchema"]["properties"].pop("versiones_anteriores", None)
    return copia


@pytest_asyncio.fixture
async def base_con_politica(base_mongo: Any) -> Any:
    await _sembrar(base_mongo, _con_historia(await _validador_real(base_mongo)))
    return base_mongo


@pytest.mark.asyncio
async def prueba_el_validador_real_declara_la_historia_de_la_politica(base_mongo: Any) -> None:
    """Si falla: la base local tiene el validador viejo. `python tareas.py validadores`."""
    esquema = (await _validador_real(base_mongo))["$jsonSchema"]

    assert esquema["additionalProperties"] is False
    assert "versiones_anteriores" in esquema["properties"]


@pytest.mark.asyncio
async def prueba_patch_sube_a_version_2_y_guarda_la_1(
    base_con_politica: Any, cliente_con_mongo: AsyncClient
) -> None:
    respuesta = await cliente_con_mongo.patch(RUTA, json={"cortes_severidad": CORTES_NUEVOS})

    assert respuesta.status_code == status.HTTP_200_OK, respuesta.text
    assert respuesta.json()["version"] == 2

    guardada = await base_con_politica[COLECCION_POLITICA].find_one({"vigente": True})
    assert guardada["version"] == 2
    assert guardada["cortes_severidad"][2]["puntaje_minimo"] == 80
    [anterior] = guardada["versiones_anteriores"]
    assert anterior["version"] == 1
    assert anterior["cortes_severidad"][2]["puntaje_minimo"] == 70
    assert anterior["vigente"] is False
    assert isinstance(anterior["reemplazada_en"], datetime)
    assert await base_con_politica[COLECCION_POLITICA].count_documents({"vigente": True}) == 1


@pytest.mark.asyncio
async def prueba_un_corte_con_hueco_da_422_y_no_escribe(
    base_con_politica: Any, cliente_con_mongo: AsyncClient
) -> None:
    con_hueco = [
        {"severidad": "MEDIA", "puntaje_minimo": 35, "puntaje_maximo": 54},
        {"severidad": "ALTA", "puntaje_minimo": 56, "puntaje_maximo": 69},
        {"severidad": "CRITICA", "puntaje_minimo": 70, "puntaje_maximo": 100},
    ]

    respuesta = await cliente_con_mongo.patch(RUTA, json={"cortes_severidad": con_hueco})

    assert respuesta.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, respuesta.text
    assert respuesta.json()["error"]["codigo"] == "parametros_invalidos"
    guardada = await base_con_politica[COLECCION_POLITICA].find_one({"vigente": True})
    assert guardada["version"] == 1
    assert "versiones_anteriores" not in guardada


@pytest.mark.asyncio
async def prueba_el_cambio_rige_en_la_siguiente_evaluacion_de_d1(
    base_con_politica: Any, cliente_con_mongo: AsyncClient
) -> None:
    """D1 relee la politica en cada evento: sin cache, el cambio rige de inmediato."""
    d1 = RepoDeteccion(base_con_politica)
    assert (await d1.leer_politica_vigente()).version == 1

    await cliente_con_mongo.patch(RUTA, json={"cortes_severidad": CORTES_NUEVOS})

    politica = await d1.leer_politica_vigente()
    assert politica.version == 2
    assert [c.puntaje_minimo for c in politica.cortes_severidad] == [35, 60, 80]

    simulada = await cliente_con_mongo.post(
        "/api/v1/reglas/simular", json=cuerpo_simulacion_txn_001()
    )
    assert simulada.json()["politica_version"] == 2
    assert simulada.json()["severidad"] == "ALTA"  # 75: ya no es CRITICA (80+)


@pytest.mark.asyncio
async def prueba_la_edicion_concurrente_no_pisa(base_con_politica: Any) -> None:
    repo = RepoReglas(base_con_politica)
    leida = await repo.politica_documento()
    assert leida is not None

    primera = await repo.editar_politica(leida, {"umbral_alerta": 35}, datetime.now(UTC))
    segunda = await repo.editar_politica(leida, {"umbral_alerta": 36}, datetime.now(UTC))

    assert primera is not None
    assert segunda is None
    guardada = await base_con_politica[COLECCION_POLITICA].find_one({"vigente": True})
    assert guardada["version"] == 2
    assert len(guardada["versiones_anteriores"]) == 1


@pytest.mark.asyncio
async def prueba_con_un_validador_viejo_la_api_explica_y_no_responde_500(
    base_mongo: Any, cliente_con_mongo: AsyncClient
) -> None:
    """El hallazgo en vivo: validador sin la historia -> codigo 121 -> forma unica."""
    await _sembrar(base_mongo, _sin_historia(await _validador_real(base_mongo)))

    respuesta = await cliente_con_mongo.patch(RUTA, json={"cortes_severidad": CORTES_NUEVOS})

    assert respuesta.status_code == status.HTTP_409_CONFLICT, respuesta.text
    error = respuesta.json()["error"]
    assert set(error) == {"codigo", "mensaje", "detalles"}
    assert error["codigo"] == "conflicto"
    assert "python tareas.py validadores" in error["mensaje"]
    guardada = await base_mongo[COLECCION_POLITICA].find_one({"vigente": True})
    assert guardada["version"] == 1
