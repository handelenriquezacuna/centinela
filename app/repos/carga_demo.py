"""Escritura del conjunto de demo.

Vive en `app/repos/` porque escribe en Mongo, y ninguna escritura va fuera de aqui.
El conjunto de datos en si NO esta aqui: esta en `pruebas/dobles/datos_demo.py`.
Este modulo no sabe que documentos carga.

Aqui se cumple la mitad de la regla de dinero que el modelo no puede cumplir: el
modelo garantiza que `monto_crc` es un entero, pero un entero de Python que cabe en
32 bits lo guarda pymongo como int32. `bson.Int64` es lo que lo convierte en el long
de 64 bits que el contrato pide y que el validador `bsonType: "long"` exige.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from bson import Int64
from pymongo import ReplaceOne
from pymongo.asynchronous.database import AsyncDatabase

from app.modelos import DocumentoBase

# Todo campo que termine asi es dinero y se escribe como long de 64 bits.
SUFIJO_DINERO = "_crc"


def a_entero_64(documento: Mapping[str, Any]) -> dict[str, Any]:
    """Copia del documento con los campos de dinero como `Int64`.

    Recorre tambien los subdocumentos y las listas, para que un campo de dinero
    dentro de un documento embebido no se escape de la regla.
    """

    def convertir(valor: Any, nombre: str = "") -> Any:
        if isinstance(valor, bool):
            return valor
        if isinstance(valor, int) and nombre.endswith(SUFIJO_DINERO):
            return Int64(valor)
        if isinstance(valor, Mapping):
            # `str(clave)` porque un StrEnum como clave (por ejemplo
            # por_severidad.CRITICA) tiene que llegar a Mongo como texto pelado.
            return {str(clave): convertir(sub, str(clave)) for clave, sub in valor.items()}
        if isinstance(valor, list):
            return [convertir(sub, nombre) for sub in valor]
        return valor

    return {str(clave): convertir(valor, str(clave)) for clave, valor in documento.items()}


async def cargar_demo(
    base: AsyncDatabase,
    datos: Mapping[str, Iterable[DocumentoBase]],
) -> dict[str, int]:
    """Escribe el conjunto de demo documento por documento y devuelve cuantos escribio.

    Reemplaza cada documento por su `_id` con `upsert`, y NO borra nada mas. Las dos
    propiedades que eso da:

    - Es reproducible: correrlo dos veces deja la base igual, porque cada documento se
      reemplaza por si mismo en vez de agregarse otra vez.
    - Es seguro: no toca ningun documento que no sea del demo. La version anterior de
      esta funcion hacia `delete_many({})` por coleccion, lo que habria borrado los
      datos de la Practica 1 que el equipo acaba de sembrar en la base. Un comando de
      demo no puede destruir el entregable de otra persona.

    Tampoco hace `dropDatabase()`: eso se llevaria los indices y los validadores
    `$jsonSchema`, que son trabajo de H-01 y H-02.
    """
    resumen: dict[str, int] = {}

    for nombre, modelos in datos.items():
        documentos = [a_entero_64(modelo.a_documento()) for modelo in modelos]
        if not documentos:
            resumen[nombre] = 0
            continue

        await base[nombre].bulk_write(
            [
                ReplaceOne({"_id": documento["_id"]}, documento, upsert=True)
                for documento in documentos
            ],
            ordered=False,
        )
        resumen[nombre] = len(documentos)

    return resumen

