"""Escritura de la carga de transacciones por lotes (T-57).

Vive en `app/repos/` porque ninguna consulta a Mongo va fuera de aqui. No valida: recibe
documentos ya validados por el modelo y los inserta con `insert_many(ordered=False)`.

Reintentable sin duplicar: el `_id` (TXN-NNNNNN) es unico, asi que volver a cargar el
mismo archivo choca con la clave duplicada (codigo 11000), y eso se cuenta como "ya
cargada" y no como error. Cualquier otro error de escritura (el validador `$jsonSchema`
rechazando el documento, por ejemplo) se devuelve con el numero de linea de origen. Un
fallo de conexion o de autenticacion no es un error de escritura de un documento: sube,
y el comando se puede volver a correr.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import BulkWriteError

from app.modelos import COLECCION_TRANSACCIONES
from app.repos.carga_demo import a_entero_64

CODIGO_CLAVE_DUPLICADA = 11000


@dataclass
class ResultadoLote:
    """Que paso con un lote. `errores` son pares (numero de linea, motivo)."""

    insertadas: int = 0
    repetidas: int = 0
    errores: list[tuple[int, str]] = field(default_factory=list)


class RepoCargaTransacciones:
    def __init__(self, base: AsyncDatabase) -> None:
        self._transacciones = base[COLECCION_TRANSACCIONES]

    async def insertar_lote(
        self, lote: Sequence[tuple[int, Mapping[str, Any]]]
    ) -> ResultadoLote:
        """Inserta `(numero_de_linea, documento)` sin orden; los fallos no frenan al resto."""
        resultado = ResultadoLote()
        if not lote:
            return resultado
        try:
            enviado = await self._transacciones.insert_many(
                [a_entero_64(documento) for _numero, documento in lote], ordered=False
            )
            resultado.insertadas = len(enviado.inserted_ids)
        except BulkWriteError as fallo:
            detalles = fallo.details or {}
            resultado.insertadas = int(detalles.get("nInserted", 0))
            for error in detalles.get("writeErrors", []):
                if error.get("code") == CODIGO_CLAVE_DUPLICADA:
                    resultado.repetidas += 1
                else:
                    numero = lote[error["index"]][0]
                    resultado.errores.append((numero, str(error.get("errmsg", "error de escritura"))))
        return resultado
