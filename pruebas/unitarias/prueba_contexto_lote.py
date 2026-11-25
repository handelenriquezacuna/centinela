"""T-62 · R-03 y R-09 contados en memoria: los mismos limites que las consultas de siempre."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.deteccion.contexto_lote import (
    HistorialPorOrigen,
    Movimiento,
    a_movimiento,
    previas_al_destino,
    ventana_de_origen,
)
from app.modelos import Transaccion

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _tx(numero: int, origen: str = "CTA-001", destino: str = "6000-0001", minutos: int = 0,
        monto: int = 1_000) -> Transaccion:
    return Transaccion(
        _id=f"TXN-{numero:06d}",
        cuenta_origen=origen,
        cuenta_destino=destino,
        monto_crc=monto,
        fecha=T0 + timedelta(minutes=minutos),
    )


def _mov(numero: int, origen: str = "CTA-001", destino: str = "6000-0001", minutos: int = 0,
         monto: int = 1_000) -> Movimiento:
    return a_movimiento(_tx(numero, origen, destino, minutos, monto))


def prueba_r03_la_cota_superior_es_estricta_y_la_inferior_inclusiva() -> None:
    actual = _tx(10)
    historial = HistorialPorOrigen(
        [
            _mov(1, minutos=-2 * 24 * 60),  # justo en el borde de 2 dias: cuenta
            _mov(2, minutos=-2 * 24 * 60 - 1),  # un minuto antes: no
            _mov(3, minutos=0),  # misma fecha exacta: no (estricto)
            _mov(4, minutos=1),  # posterior: no
            _mov(5, minutos=-10),  # cuenta
        ]
    )

    assert previas_al_destino(actual, {"CTA-001"}, 2, historial) == 2


def prueba_r03_cuenta_todas_las_cuentas_del_cliente_y_solo_el_mismo_destino() -> None:
    actual = _tx(10)
    historial = HistorialPorOrigen(
        [
            _mov(1, origen="CTA-001", minutos=-5),
            _mov(2, origen="CTA-002", minutos=-5),  # cuenta hermana: cuenta
            _mov(3, origen="CTA-009", minutos=-5),  # de otro cliente: no
            _mov(4, origen="CTA-001", destino="6000-0002", minutos=-5),  # otro destino: no
        ]
    )

    assert previas_al_destino(actual, {"CTA-001", "CTA-002"}, 90, historial) == 2


def prueba_r03_no_se_cuenta_a_si_misma() -> None:
    actual = _tx(10, minutos=-1)
    historial = HistorialPorOrigen([a_movimiento(actual)])

    assert previas_al_destino(actual, {"CTA-001"}, 90, historial) == 0


def prueba_r09_la_ventana_incluye_el_borde_y_la_misma_fecha() -> None:
    actual = _tx(10, monto=500)
    historial = HistorialPorOrigen(
        [
            _mov(1, minutos=-30, monto=100),  # borde inferior: cuenta
            _mov(2, minutos=-31, monto=100),  # fuera
            _mov(3, minutos=0, monto=200),  # misma fecha exacta: cuenta (cota inclusiva)
            _mov(4, minutos=1, monto=900),  # posterior: no
            _mov(5, origen="CTA-002", minutos=-5, monto=900),  # otro origen: no
        ]
    )

    ventana = ventana_de_origen(actual, 30, historial)

    assert ventana.cantidad == 3, "las dos de la ventana y la propia"
    assert ventana.monto_acumulado_crc == 100 + 200 + 500
    assert ventana.ventana_minutos == 30


def prueba_r09_una_transaccion_anterior_del_mismo_lote_cuenta_para_las_siguientes() -> None:
    primera = _tx(1, minutos=0, monto=100)
    segunda = _tx(2, minutos=5, monto=200)
    tercera = _tx(3, minutos=10, monto=400)
    historial = HistorialPorOrigen()
    for transaccion in (primera, segunda, tercera):
        historial.agregar(a_movimiento(transaccion))

    assert ventana_de_origen(primera, 30, historial).cantidad == 1
    assert ventana_de_origen(segunda, 30, historial).cantidad == 2
    assert ventana_de_origen(tercera, 30, historial).monto_acumulado_crc == 700


def prueba_el_historial_no_repite_una_transaccion() -> None:
    historial = HistorialPorOrigen([_mov(1), _mov(1)])

    assert len(historial.de("CTA-001")) == 1
