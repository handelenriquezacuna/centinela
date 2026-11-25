"""El manifiesto de un respaldo y la comparacion de conteos de la verificacion.

Todo puro: ni Mongo ni procesos, para que las pruebas unitarias lo cubran sin infraestructura.

El manifiesto es un JSON junto al archivo (`RSP-...json` junto a `RSP-....gz`) y es la
unica memoria de un respaldo: no hay coleccion nueva (el contrato fija 13). Se escribe
completo con un renombrado atomico, asi un corte a la mitad no deja un JSON truncado.

Conteos: se miden ANTES y DESPUES del dump, porque la base viva sigue recibiendo
transacciones mientras `mongodump` lee. Si no hubo escrituras (lo normal en una base en
reposo) los dos conteos son iguales y la verificacion exige igualdad exacta; si hubo, se
acepta lo restaurado entre los dos conteos, que es lo que un dump sin `--oplog` puede
garantizar. Una coleccion que falta en lo restaurado cuenta como 0.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modelos.base import ESQUEMA_VERSION, AgenteId

# RSP-20261124T153012Z: legible, ordenable por nombre y sin caracteres que un nombre de
# archivo o una URL rechacen. Es tambien el patron de la ruta de la API.
PATRON_ID = r"^RSP-\d{8}T\d{6}Z$"
_PATRON_ID = re.compile(PATRON_ID)

ESQUEMA_MANIFIESTO = 1

ResultadoVerificacion = Literal["correcta", "fallida"]


class Diferencia(BaseModel):
    """Una coleccion cuyo conteo restaurado no es el del manifiesto."""

    model_config = ConfigDict(extra="forbid")

    coleccion: str
    esperados_desde: int = Field(ge=0)
    esperados_hasta: int = Field(ge=0)
    restaurados: int = Field(ge=0)


class Verificacion(BaseModel):
    """El resultado de restaurar el archivo en una base temporal y contar."""

    model_config = ConfigDict(extra="forbid")

    verificado_utc: datetime
    verificado_por: AgenteId
    resultado: ResultadoVerificacion
    base_temporal: str
    # La base temporal se borra siempre (aunque la verificacion falle); esto lo confirma.
    base_temporal_borrada: bool
    duracion_segundos: float = Field(ge=0)
    documentos_restaurados: dict[str, int] = Field(default_factory=dict)
    diferencias: list[Diferencia] = Field(default_factory=list)
    detalle: str | None = None


class Manifiesto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    esquema_version: int = ESQUEMA_VERSION
    respaldo_id: str = Field(pattern=PATRON_ID)
    creado_utc: datetime
    creado_por: AgenteId
    base: str
    archivo: str
    tamano_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    herramienta: str  # la linea de version de mongodump
    # Conteo justo antes del dump y justo despues; ver el docstring del modulo.
    documentos_por_coleccion: dict[str, int]
    documentos_al_terminar: dict[str, int]
    verificacion: Verificacion | None = None

    @property
    def total_documentos(self) -> int:
        return sum(self.documentos_por_coleccion.values())


def nuevo_id(ahora: datetime | None = None) -> str:
    momento = (ahora or datetime.now(UTC)).astimezone(UTC)
    return "RSP-" + momento.strftime("%Y%m%dT%H%M%SZ")


def es_id_valido(valor: str) -> bool:
    return _PATRON_ID.fullmatch(valor) is not None


def comparar_conteos(
    antes: dict[str, int], despues: dict[str, int], restaurados: dict[str, int]
) -> list[Diferencia]:
    """Colecciones cuyo conteo restaurado queda fuera de [min(antes, despues), max(...)].

    Una coleccion presente en cualquiera de los tres mapas se revisa. Sin diferencias =
    lista vacia = la verificacion es correcta.
    """
    diferencias: list[Diferencia] = []
    for coleccion in sorted(set(antes) | set(despues) | set(restaurados)):
        a, b = antes.get(coleccion, 0), despues.get(coleccion, 0)
        desde, hasta = min(a, b), max(a, b)
        obtenidos = restaurados.get(coleccion, 0)
        if not desde <= obtenidos <= hasta:
            diferencias.append(
                Diferencia(
                    coleccion=coleccion,
                    esperados_desde=desde,
                    esperados_hasta=hasta,
                    restaurados=obtenidos,
                )
            )
    return diferencias


def ruta_manifiesto(directorio: Path, respaldo_id: str) -> Path:
    return directorio / f"{respaldo_id}.json"


def escribir(directorio: Path, manifiesto: Manifiesto) -> Path:
    """Escribe el manifiesto con un renombrado atomico (nunca queda un JSON a medias)."""
    destino = ruta_manifiesto(directorio, manifiesto.respaldo_id)
    temporal = destino.with_suffix(".json.tmp")
    temporal.write_text(
        json.dumps(manifiesto.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporal, destino)
    return destino


def leer(ruta: Path) -> Manifiesto:
    return Manifiesto.model_validate_json(ruta.read_text(encoding="utf-8"))
