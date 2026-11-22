"""H-10 · Calculo de efectividad, sin base de datos.

Casos con verdaderos positivos, falsos positivos y falsos negativos conocidos, y los
bordes donde una metrica no tiene denominador.
"""

from __future__ import annotations

import pytest

from app.deteccion.efectividad import (
    SIN_PATRON,
    FilaEtiquetada,
    calcular_efectividad,
    formatear_tabla,
    razon,
)

ATIPICO = "monto_atipico_destino_nuevo"
RAFAGA = "rafaga_fragmentada"
CADENA = "cadena_mulas"


def _legitimas(total: int, alertadas: int) -> FilaEtiquetada:
    return FilaEtiquetada(False, None, None, total, alertadas)


def _episodio(patron: str, episodio: str, total: int, alertadas: int) -> FilaEtiquetada:
    return FilaEtiquetada(True, patron, episodio, total, alertadas)


def prueba_cuenta_detectados_perdidos_y_falsos_positivos() -> None:
    filas = [
        _legitimas(1000, 4),  # 4 falsos positivos, 996 verdaderos negativos
        _episodio(ATIPICO, "EPI-0001", 1, 1),
        _episodio(ATIPICO, "EPI-0002", 1, 0),
        _episodio(RAFAGA, "EPI-0003", 5, 3),
        _episodio(CADENA, "EPI-0004", 4, 1),
    ]

    reporte = calcular_efectividad(filas, alertas_totales=10)

    assert reporte.transacciones_etiquetadas == 1011
    assert (reporte.fraudes, reporte.legitimas) == (11, 1000)
    assert (reporte.detectados, reporte.perdidos) == (5, 6)
    assert (reporte.falsos_positivos, reporte.verdaderos_negativos) == (4, 996)
    assert reporte.alertas_sin_etiqueta == 10 - 9
    assert reporte.precision == round(5 / 9, 4)
    assert reporte.exhaustividad == round(5 / 11, 4)
    assert reporte.detectados + reporte.perdidos == reporte.fraudes


def prueba_desglosa_por_patron_con_episodios() -> None:
    filas = [
        _legitimas(50, 0),
        _episodio(RAFAGA, "EPI-0001", 5, 5),
        _episodio(RAFAGA, "EPI-0002", 4, 0),
        _episodio(CADENA, "EPI-0003", 4, 1),
    ]

    por_patron = {
        fila.patron: fila
        for fila in calcular_efectividad(filas, alertas_totales=6).por_patron
    }

    assert list(por_patron) == [CADENA, RAFAGA], "orden alfabetico, estable"
    rafaga = por_patron[RAFAGA]
    assert (rafaga.fraudes, rafaga.detectados, rafaga.perdidos) == (9, 5, 4)
    assert rafaga.exhaustividad == round(5 / 9, 4)
    assert (rafaga.episodios, rafaga.episodios_detectados) == (2, 1)
    cadena = por_patron[CADENA]
    assert (cadena.episodios, cadena.episodios_detectados) == (1, 1)
    assert cadena.exhaustividad == 0.25


def prueba_sin_alertas_la_precision_no_se_inventa() -> None:
    """Sin alertas no hay con que medir precision: `None`, no 0 ni 1."""
    reporte = calcular_efectividad(
        [_legitimas(100, 0), _episodio(ATIPICO, "EPI-0001", 2, 0)], alertas_totales=0
    )

    assert reporte.precision is None
    assert reporte.exhaustividad == 0.0
    assert reporte.perdidos == 2


def prueba_sin_fraude_la_exhaustividad_no_se_inventa() -> None:
    reporte = calcular_efectividad([_legitimas(100, 3)], alertas_totales=3)

    assert reporte.exhaustividad is None
    assert reporte.precision == 0.0
    assert reporte.por_patron == []


def prueba_sin_filas_todo_es_cero_o_no_disponible() -> None:
    reporte = calcular_efectividad([], alertas_totales=0)

    assert reporte.transacciones_etiquetadas == 0
    assert reporte.precision is None
    assert reporte.exhaustividad is None
    assert "n/d" in formatear_tabla(reporte)


def prueba_razon_con_denominador_cero_es_none() -> None:
    assert razon(0, 0) is None
    assert razon(3, 0) is None
    assert razon(1, 3) == 0.3333


def prueba_el_fraude_sin_patron_no_se_pierde() -> None:
    reporte = calcular_efectividad(
        [FilaEtiquetada(True, None, None, 2, 1)], alertas_totales=1
    )

    assert [fila.patron for fila in reporte.por_patron] == [SIN_PATRON]


def prueba_mas_alertas_etiquetadas_que_el_total_es_un_error() -> None:
    with pytest.raises(ValueError, match="momentos distintos"):
        calcular_efectividad([_legitimas(10, 5)], alertas_totales=4)


def prueba_una_fila_con_mas_alertas_que_transacciones_es_un_error() -> None:
    with pytest.raises(ValueError, match="una sola alerta"):
        FilaEtiquetada(True, ATIPICO, "EPI-0001", 1, 2)


def prueba_la_tabla_trae_total_y_cada_patron() -> None:
    reporte = calcular_efectividad(
        [_legitimas(100, 1), _episodio(RAFAGA, "EPI-0001", 4, 4)], alertas_totales=5
    )

    tabla = formatear_tabla(reporte)

    assert RAFAGA in tabla
    assert "total" in tabla
    assert "precision: 80.0 %" in tabla
    assert "exhaustividad: 100.0 %" in tabla
    assert "sin etiqueta: 0" in tabla
