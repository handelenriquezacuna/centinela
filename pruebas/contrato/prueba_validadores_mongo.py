"""Los modelos contra los validadores REALES de la base del equipo.

Estas pruebas necesitan el replica set y se saltan solas si no esta.

Como funciona, y por que asi: se leen los validadores `$jsonSchema` de la base de
trabajo (`antifraude`, que siembran H-01 y H-02), se copian a una base desechable, y
ahi se comprueba que acepta los documentos del demo y que rechaza los malformados.
Asi la prueba verifica el contrato real sin escribir una sola linea en la base del
equipo.

Es la prueba que detecta la divergencia mas cara del proyecto: que el modelo Pydantic
y el validador de Mongo digan cosas distintas sobre el mismo documento.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from bson import Int64
from pymongo.errors import WriteError

from app.repos.carga_demo import a_entero_64
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from pruebas.dobles.datos_demo import DATOS_DEMO

pytestmark = pytest.mark.mongo

BASE_DE_TRABAJO = "antifraude"


async def _preparar(base_mongo: Any, coleccion: str) -> Any:
    """Copia el validador real de `coleccion` a la base de pruebas y la devuelve."""
    de_trabajo = base_mongo.client[BASE_DE_TRABAJO]
    validadores = await obtener_validadores(de_trabajo)

    if coleccion not in validadores:
        pytest.skip(
            f"la coleccion {coleccion} todavia no tiene validador en {BASE_DE_TRABAJO}; "
            "los escriben H-01 y H-02"
        )

    await recrear_con_validador(base_mongo, coleccion, validadores[coleccion])
    return base_mongo[coleccion]


@pytest.mark.asyncio
@pytest.mark.parametrize("coleccion", sorted(DATOS_DEMO))
async def prueba_el_validador_real_acepta_los_documentos_del_demo(
    base_mongo: Any, coleccion: str
) -> None:
    """Cada documento que produce un modelo tiene que pasar el validador de su coleccion.

    Si esta falla, el modelo Pydantic y el `$jsonSchema` no dicen lo mismo, y alguien
    va a descubrirlo escribiendo en produccion.
    """
    destino = await _preparar(base_mongo, coleccion)

    documentos = [a_entero_64(modelo.a_documento()) for modelo in DATOS_DEMO[coleccion]]
    resultado = await destino.insert_many(documentos)

    assert len(resultado.inserted_ids) == len(documentos)


@pytest.mark.asyncio
@pytest.mark.parametrize("coleccion", sorted(DATOS_DEMO))
async def prueba_el_validador_real_rechaza_un_documento_incompleto(
    base_mongo: Any, coleccion: str
) -> None:
    """Le falta un campo obligatorio: la base tiene que negarse.

    Es la otra mitad de H-02: que la base rechace basura, no que la acepte con cara
    de que todo va bien.
    """
    destino = await _preparar(base_mongo, coleccion)

    validadores = await obtener_validadores(base_mongo.client[BASE_DE_TRABAJO])
    obligatorios = [
        campo
        for campo in validadores[coleccion]["$jsonSchema"].get("required", [])
        if campo != "_id"
    ]
    if not obligatorios:
        pytest.skip(f"{coleccion} no declara campos obligatorios aparte de _id")

    completo = a_entero_64(DATOS_DEMO[coleccion][0].a_documento())
    incompleto = {k: v for k, v in completo.items() if k != obligatorios[0]}

    with pytest.raises(WriteError):
        await destino.insert_one(incompleto)


@pytest.mark.asyncio
async def prueba_el_dinero_como_entero_de_32_bits_se_rechaza(base_mongo: Any) -> None:
    """La mitad del contrato de dinero que el modelo Pydantic NO puede garantizar.

    `monto_crc` declara `bsonType: "long"`. Un int de Python que cabe en 32 bits lo
    guarda pymongo como int32, asi que el documento se rechaza aunque el modelo diga
    que el campo es un entero. Hay que envolverlo en `bson.Int64`
    (`NumberLong(...)` en mongosh). Sin esta prueba, "entero de 64 bits" es una frase
    en un documento y no una propiedad del sistema.
    """
    destino = await _preparar(base_mongo, "transacciones")

    documento = DATOS_DEMO["transacciones"][0].a_documento()

    # Tal cual sale del modelo: int de Python, que viaja como int32.
    assert isinstance(documento["monto_crc"], int)
    with pytest.raises(WriteError) as fallo:
        await destino.insert_one({**documento, "_id": "TXN-901"})
    assert "monto_crc" in str(fallo.value) or "long" in str(fallo.value).lower()

    # Envuelto en Int64, la misma cifra pasa.
    convertido = a_entero_64(documento)
    assert isinstance(convertido["monto_crc"], Int64)
    await destino.insert_one({**convertido, "_id": "TXN-902"})


@pytest.mark.asyncio
async def prueba_una_fecha_como_texto_se_rechaza(base_mongo: Any) -> None:
    """Las fechas son ISODate, no texto. La excepcion es indicadores_diarios.fecha."""
    destino = await _preparar(base_mongo, "transacciones")

    documento = a_entero_64(DATOS_DEMO["transacciones"][0].a_documento())

    with pytest.raises(WriteError):
        await destino.insert_one(
            {**documento, "_id": "TXN-903", "fecha": "2026-09-24T22:47:00-06:00"}
        )


@pytest.mark.asyncio
async def prueba_un_id_de_otra_coleccion_se_rechaza(base_mongo: Any) -> None:
    """Un CLI-001 donde va un TXN-001 no es un detalle: es una referencia rota."""
    destino = await _preparar(base_mongo, "alertas")

    documento = a_entero_64(DATOS_DEMO["alertas"][0].a_documento())

    with pytest.raises(WriteError):
        await destino.insert_one(
            {**documento, "_id": "ALR-901", "transaccion_id": "CLI-001"}
        )


@pytest.mark.asyncio
async def prueba_el_estado_fuera_de_la_maquina_se_rechaza(base_mongo: Any) -> None:
    """`resuelta` no existe en la maquina de estados, y la base tampoco lo acepta.

    docs/01-vision-y-alcance.md lo mencionaba; la maquina real nunca lo tuvo.
    """
    destino = await _preparar(base_mongo, "alertas")

    documento = a_entero_64(DATOS_DEMO["alertas"][0].a_documento())

    with pytest.raises(WriteError):
        await destino.insert_one(
            {**documento, "_id": "ALR-902", "estado": "resuelta"}
        )


@pytest.mark.asyncio
async def prueba_la_severidad_baja_no_existe(base_mongo: Any) -> None:
    """Bajo umbral no se crea alerta (UC-02), asi que BAJA no puede existir."""
    destino = await _preparar(base_mongo, "alertas")

    documento = a_entero_64(DATOS_DEMO["alertas"][0].a_documento())

    with pytest.raises(WriteError):
        await destino.insert_one({**documento, "_id": "ALR-903", "severidad": "BAJA"})


@pytest.mark.asyncio
async def prueba_la_fecha_del_indicador_es_texto_y_no_isodate(base_mongo: Any) -> None:
    """La unica excepcion declarada a la regla de fechas, verificada al reves."""
    destino = await _preparar(base_mongo, "indicadores_diarios")

    documento = a_entero_64(DATOS_DEMO["indicadores_diarios"][0].a_documento())

    assert isinstance(documento["fecha"], str)
    assert documento["_id"] == documento["fecha"]

    with pytest.raises(WriteError):
        await destino.insert_one(
            {**documento, "_id": "x", "fecha": datetime(2026, 9, 24, tzinfo=UTC)}
        )
