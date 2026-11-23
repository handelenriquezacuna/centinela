"""Carga por lotes de transacciones desde JSONL: una transaccion por linea.

Formato de cada linea: el documento de `transacciones` tal como esta en la base, en JSON
(la fecha en ISO 8601 con zona, por ejemplo `2026-11-23T14:05:00Z` o `-06:00`):

    {"_id": "TXN-000001", "cuenta_origen": "CTA-001", "cuenta_destino": "6033-9001",
     "monto_crc": 150000, "fecha": "2026-11-23T14:05:00-06:00"}

Cada linea se valida con el modelo `Transaccion` de la app ANTES de insertar: monto
entero (nunca flotante), fecha con zona (se normaliza a UTC; sin zona se rechaza),
identificador `TXN-`. Una linea mala se rechaza con su numero y no frena a las demas.

Se inserta en lotes de `TAMANO_LOTE` con `insert_many(ordered=False)` (ver
`app/repos/carga_transacciones.py`). Volver a correr el mismo archivo no duplica: las que
ya estaban se cuentan como repetidas. Con D1 corriendo, cada transaccion cargada se
evalua por el change stream igual que una insertada por el generador.

Este modulo es logica pura mas un repositorio inyectado: no abre la base.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from app.modelos import Transaccion
from app.repos.carga_transacciones import ResultadoLote

TAMANO_LOTE = 1000


class ProtocoloRepoCarga(Protocol):
    async def insertar_lote(
        self, lote: list[tuple[int, dict[str, Any]]]
    ) -> ResultadoLote: ...


class LineaInvalida(ValueError):
    """La linea no es una transaccion valida. El mensaje se muestra tal cual."""


@dataclass(frozen=True)
class LineaRechazada:
    numero: int
    motivo: str


@dataclass
class ResumenCarga:
    cargadas: int = 0
    repetidas: int = 0
    rechazadas: list[LineaRechazada] = field(default_factory=list)
    segundos: float = 0.0

    @property
    def transacciones_por_segundo(self) -> float:
        return self.cargadas / self.segundos if self.segundos > 0 else 0.0


def interpretar_linea(texto: str) -> Transaccion:
    """Una linea JSONL -> `Transaccion`, o `LineaInvalida` con el motivo en claro."""
    try:
        contenido = json.loads(texto)
    except json.JSONDecodeError as fallo:
        raise LineaInvalida(f"no es JSON valido ({fallo.msg}, columna {fallo.colno})") from fallo
    if not isinstance(contenido, dict):
        raise LineaInvalida("la linea tiene que ser un objeto JSON")
    try:
        return Transaccion.model_validate(contenido)
    except ValidationError as fallo:
        motivos = "; ".join(
            f"{'.'.join(str(parte) for parte in error['loc'])}: {error['msg']}"
            for error in fallo.errors(include_url=False)
        )
        raise LineaInvalida(motivos) from fallo


async def cargar(
    repo: ProtocoloRepoCarga,
    lineas: Iterable[str],
    *,
    tamano_lote: int = TAMANO_LOTE,
) -> ResumenCarga:
    """Valida y carga las lineas. Las vacias se saltan (cuentan en la numeracion)."""
    resumen = ResumenCarga()
    lote: list[tuple[int, dict[str, Any]]] = []
    inicio = time.perf_counter()

    async def volcar() -> None:
        resultado = await repo.insertar_lote(lote)
        resumen.cargadas += resultado.insertadas
        resumen.repetidas += resultado.repetidas
        resumen.rechazadas.extend(
            LineaRechazada(numero, f"la base la rechazo: {motivo}")
            for numero, motivo in resultado.errores
        )
        lote.clear()

    for numero, texto in enumerate(lineas, start=1):
        if not texto.strip():
            continue
        try:
            transaccion = interpretar_linea(texto)
        except LineaInvalida as fallo:
            resumen.rechazadas.append(LineaRechazada(numero, str(fallo)))
            continue
        lote.append((numero, transaccion.a_documento()))
        if len(lote) >= tamano_lote:
            await volcar()
    if lote:
        await volcar()
    resumen.segundos = time.perf_counter() - inicio
    resumen.rechazadas.sort(key=lambda rechazada: rechazada.numero)
    return resumen
