"""T-62 · Partes puras del contexto por lote de D1: R-03 (destinos) y R-09 (ventana).

Hasta T-62 D1 le preguntaba a Mongo, por cada transaccion, cuantas transferencias previas
habia al mismo destino (R-03) y cuantas del mismo origen en la ventana (R-09). Por lote se
lee UNA vez el historial que cubre a todas las transacciones del lote y estas funciones
cuentan en memoria. El resultado tiene que ser IDENTICO al de la consulta de siempre, asi
que reproducen sus filtros al pie de la letra:

- R-03: transferencias de cualquiera de las cuentas del cliente al mismo destino, con
  `fecha` en `[fecha - dias, fecha)` (cota superior estricta) y sin la propia transaccion.
- R-09: transferencias del mismo origen con `fecha` en `[fecha - minutos, fecha]` (cota
  superior inclusiva), sin la propia, mas la propia sumada aparte.

La fecha de corte es la de CADA transaccion, no la del lote: el orden dentro del lote no
cambia nada. Una transferencia del mismo lote con fecha anterior cuenta, como cuenta hoy
cuando la consulta la encuentra ya insertada; las del lote se agregan al historial aunque
la lectura no las haya devuelto, para no depender de lo que el servidor ya muestre.

No hay I/O: se prueba sin base de datos.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from app.deteccion.evaluador import VentanaOrigen
from app.modelos import Transaccion


class Movimiento(NamedTuple):
    """Lo minimo de una transferencia para contar: sin el documento completo."""

    transaccion_id: str
    cuenta_origen: str
    cuenta_destino: str
    fecha: datetime
    monto_crc: int


def _utc(fecha: datetime) -> datetime:
    return fecha if fecha.tzinfo is not None else fecha.replace(tzinfo=UTC)


def a_movimiento(transaccion: Transaccion) -> Movimiento:
    return Movimiento(
        transaccion.transaccion_id,
        transaccion.cuenta_origen,
        transaccion.cuenta_destino,
        _utc(transaccion.fecha),
        transaccion.monto_crc,
    )


def movimiento_de_documento(documento: dict[str, object]) -> Movimiento:
    """Un documento de `transacciones` leido con proyeccion minima."""
    fecha = documento["fecha"]
    assert isinstance(fecha, datetime)
    return Movimiento(
        str(documento["_id"]),
        str(documento["cuenta_origen"]),
        str(documento["cuenta_destino"]),
        _utc(fecha),
        int(documento["monto_crc"]),  # type: ignore[call-overload]
    )


class HistorialPorOrigen:
    """Movimientos indexados por `cuenta_origen`, sin repetir ninguna transaccion."""

    def __init__(self, movimientos: Iterable[Movimiento] = ()) -> None:
        self._vistos: set[str] = set()
        self._por_origen: dict[str, list[Movimiento]] = defaultdict(list)
        for movimiento in movimientos:
            self.agregar(movimiento)

    def agregar(self, movimiento: Movimiento) -> None:
        if movimiento.transaccion_id in self._vistos:
            return
        self._vistos.add(movimiento.transaccion_id)
        self._por_origen[movimiento.cuenta_origen].append(movimiento)

    def de(self, cuenta_origen: str) -> list[Movimiento]:
        return self._por_origen.get(cuenta_origen, [])


def previas_al_destino(
    transaccion: Transaccion,
    cuentas_del_cliente: Collection[str],
    dias: int,
    historial: HistorialPorOrigen,
) -> int:
    """R-03: transferencias previas del cliente al destino de `transaccion`."""
    fecha = _utc(transaccion.fecha)
    desde = fecha - timedelta(days=dias)
    return sum(
        1
        for cuenta in cuentas_del_cliente
        for movimiento in historial.de(cuenta)
        if movimiento.cuenta_destino == transaccion.cuenta_destino
        and desde <= movimiento.fecha < fecha
        and movimiento.transaccion_id != transaccion.transaccion_id
    )


def ventana_de_origen(
    transaccion: Transaccion, minutos: int, historial: HistorialPorOrigen
) -> VentanaOrigen:
    """R-09: la propia transaccion y las del mismo origen en los `minutos` anteriores."""
    fecha = _utc(transaccion.fecha)
    desde = fecha - timedelta(minutes=minutos)
    cantidad = 0
    monto = 0
    for movimiento in historial.de(transaccion.cuenta_origen):
        if movimiento.transaccion_id != transaccion.transaccion_id and (
            desde <= movimiento.fecha <= fecha
        ):
            cantidad += 1
            monto += movimiento.monto_crc
    return VentanaOrigen(
        ventana_minutos=minutos,
        cantidad=cantidad + 1,
        monto_acumulado_crc=monto + transaccion.monto_crc,
    )
