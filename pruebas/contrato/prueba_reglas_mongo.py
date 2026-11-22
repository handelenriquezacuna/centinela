"""`/api/v1/reglas` contra MongoDB de verdad (H-16).

Necesita el replica set; se salta sola si no esta. Lo que el doble no puede probar:

- que el validador `$jsonSchema` REAL de `reglas_deteccion` (el de
  `scripts/semilla/01-colecciones.js`) acepta el documento versionado;
- que la escritura filtrada por version de verdad no pisa una edicion concurrente;
- que el catalogo REAL de `03-catalogos.js` da 75 CRITICA para TXN-001 por la ruta de
  simulacion, y que el dinero de `parametros` sigue siendo long despues de editar.

Igual que `prueba_validadores_mongo.py`: el validador y el catalogo se leen de la base
de trabajo (`antifraude`) y se copian a la base de pruebas. No se escribe en la base
del equipo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from bson import Int64
from fastapi import status
from httpx import AsyncClient
from pymongo.errors import WriteError

from app.api.rutas.reglas import _exigir_tipo_y_parametros
from app.modelos import COLECCION_POLITICA
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.reglas import COLECCION_REGLAS, RepoReglas
from pruebas.dobles.catalogo_reglas import cuerpo_simulacion_txn_001

pytestmark = pytest.mark.mongo

BASE_DE_TRABAJO = "antifraude"
RUTA = "/api/v1/reglas"


@pytest_asyncio.fixture
async def base_con_reglas(base_mongo: Any) -> Any:
    """Base de pruebas con el validador, el indice de codigo y el catalogo reales."""
    trabajo = base_mongo.client[BASE_DE_TRABAJO]
    validadores = await obtener_validadores(trabajo)
    reglas = await trabajo[COLECCION_REGLAS].find().to_list(length=None)
    politicas = await trabajo[COLECCION_POLITICA].find().to_list(length=None)
    if COLECCION_REGLAS not in validadores or not reglas or not politicas:
        pytest.skip(
            "antifraude no tiene sembrado reglas_deteccion o politica_deteccion: correr "
            "scripts/semilla/01, 02 y 03 (python tareas.py datos-demo)"
        )

    await recrear_con_validador(base_mongo, COLECCION_REGLAS, validadores[COLECCION_REGLAS])
    await base_mongo[COLECCION_REGLAS].create_index(
        "codigo", unique=True, name="idx_reglas_codigo"
    )
    await base_mongo[COLECCION_REGLAS].insert_many(reglas)

    if COLECCION_POLITICA in validadores:
        await recrear_con_validador(
            base_mongo, COLECCION_POLITICA, validadores[COLECCION_POLITICA]
        )
    else:  # pragma: no cover - 01 siempre la declara
        await base_mongo.drop_collection(COLECCION_POLITICA)
    await base_mongo[COLECCION_POLITICA].insert_many(politicas)
    return base_mongo


@pytest.mark.asyncio
async def prueba_el_validador_real_declara_la_historia(base_con_reglas: Any) -> None:
    """Sin esto, la edicion de abajo pasaria contra un validador viejo por accidente."""
    validador = (await obtener_validadores(base_con_reglas))[COLECCION_REGLAS]
    esquema = validador["$jsonSchema"]

    assert esquema["additionalProperties"] is False
    assert "versiones_anteriores" in esquema["properties"]


@pytest.mark.asyncio
async def prueba_editar_reg_001_sube_a_version_2_y_guarda_la_1(
    base_con_reglas: Any, cliente_con_mongo: AsyncClient
) -> None:
    respuesta = await cliente_con_mongo.patch(f"{RUTA}/REG-001", json={"umbral": 20})

    assert respuesta.status_code == status.HTTP_200_OK, respuesta.text
    assert respuesta.json()["version"] == 2

    guardada = await base_con_reglas[COLECCION_REGLAS].find_one({"_id": "REG-001"})
    assert guardada["version"] == 2
    assert guardada["umbral"] == 20
    [anterior] = guardada["versiones_anteriores"]
    assert anterior["version"] == 1
    assert anterior["umbral"] == 10
    assert anterior["peso"] == 35
    assert anterior["reemplazada_en"].tzinfo is not None


@pytest.mark.asyncio
async def prueba_el_validador_real_rechaza_una_historia_mal_formada(
    base_con_reglas: Any,
) -> None:
    """La otra mitad: el validador no acepta cualquier cosa en `versiones_anteriores`."""
    with pytest.raises(WriteError):
        await base_con_reglas[COLECCION_REGLAS].update_one(
            {"_id": "REG-001"},
            {"$push": {"versiones_anteriores": {"version": 1, "umbral": 10}}},
        )


@pytest.mark.asyncio
async def prueba_desactivar_y_editar_dinero_contra_mongo(
    base_con_reglas: Any, cliente_con_mongo: AsyncClient
) -> None:
    apagada = await cliente_con_mongo.patch(f"{RUTA}/REG-003", json={"activa": False})
    assert apagada.status_code == status.HTTP_200_OK, apagada.text
    assert apagada.json()["activa"] is False

    editada = await cliente_con_mongo.patch(
        f"{RUTA}/REG-005",
        json={"parametros": {"ventana_minutos": 15, "monto_acumulado_crc": 200_000}},
    )
    assert editada.status_code == status.HTTP_200_OK, editada.text

    guardada = await base_con_reglas[COLECCION_REGLAS].find_one({"_id": "REG-005"})
    assert isinstance(guardada["parametros"]["monto_acumulado_crc"], Int64)
    assert isinstance(
        guardada["versiones_anteriores"][0]["parametros"]["monto_acumulado_crc"], Int64
    )


@pytest.mark.asyncio
async def prueba_la_edicion_concurrente_no_pisa_contra_mongo(base_con_reglas: Any) -> None:
    """Dos supervisores leen la version 1; el segundo en escribir no encuentra nada."""
    repo = RepoReglas(base_con_reglas)
    leida = await repo.obtener("REG-001")
    assert leida is not None

    primera = await repo.editar(leida, {"umbral": 20}, datetime.now(UTC))
    segunda = await repo.editar(leida, {"umbral": 30}, datetime.now(UTC))

    assert primera is not None and primera["version"] == 2
    assert segunda is None
    guardada = await repo.obtener("REG-001")
    assert guardada is not None
    assert guardada["umbral"] == 20
    assert len(guardada["versiones_anteriores"]) == 1


@pytest.mark.asyncio
async def prueba_crear_contra_el_validador_y_el_indice_reales(
    base_con_reglas: Any, cliente_con_mongo: AsyncClient
) -> None:
    creada = await cliente_con_mongo.post(
        RUTA,
        json={
            "codigo": "R-13",
            "descripcion": "Rafaga corta",
            "tipo": "velocidad",
            "umbral": 2,
            "parametros": {"ventana_minutos": 10, "monto_acumulado_crc": 100_000},
            "peso": 10,
            "activa": False,
        },
    )
    assert creada.status_code == status.HTTP_201_CREATED, creada.text
    assert creada.json()["_id"] == "REG-007"
    guardada = await base_con_reglas[COLECCION_REGLAS].find_one({"_id": "REG-007"})
    assert isinstance(guardada["parametros"]["monto_acumulado_crc"], Int64)

    duplicada = await cliente_con_mongo.post(
        RUTA,
        json={"codigo": "R-01", "descripcion": "x", "tipo": "monto", "umbral": 1, "peso": 1},
    )
    assert duplicada.status_code == status.HTTP_409_CONFLICT
    assert duplicada.json()["error"]["codigo"] == "conflicto"


@pytest.mark.asyncio
async def prueba_el_catalogo_sembrado_pasa_la_validacion_de_tipos(
    base_con_reglas: Any,
) -> None:
    """Las 6 reglas de H-07 se pueden editar: ningun parametro sobra ni falta."""
    reglas = await RepoReglas(base_con_reglas).listar()

    assert len(reglas) == 6
    for regla in reglas:
        _exigir_tipo_y_parametros(regla["tipo"], regla.get("parametros", {}))


@pytest.mark.asyncio
async def prueba_simular_txn_001_da_75_critica_y_el_umbral_lo_baja(
    base_con_reglas: Any, cliente_con_mongo: AsyncClient
) -> None:
    cuerpo = cuerpo_simulacion_txn_001()
    base = await cliente_con_mongo.post(f"{RUTA}/simular", json=cuerpo)

    assert base.status_code == status.HTTP_200_OK, base.text
    evaluacion = base.json()
    assert evaluacion["puntaje"] == 75
    assert evaluacion["severidad"] == "CRITICA"
    assert [d["codigo"] for d in evaluacion["reglas_disparadas"]] == ["R-01", "R-03", "R-05"]

    r01 = (await cliente_con_mongo.get(f"{RUTA}/REG-001")).json()
    r01["umbral"] = 20
    del r01["versiones_anteriores"]
    propuesta = (
        await cliente_con_mongo.post(f"{RUTA}/simular", json={**cuerpo, "reglas": [r01]})
    ).json()

    assert propuesta["puntaje"] == 40
    assert propuesta["severidad"] == "MEDIA"
    guardada = await base_con_reglas[COLECCION_REGLAS].find_one({"_id": "REG-001"})
    assert guardada["umbral"] == 10
    assert guardada["version"] == 1
