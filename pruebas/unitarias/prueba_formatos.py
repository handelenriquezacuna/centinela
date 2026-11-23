"""Formatos de Costa Rica del portal: un filtro por concepto, probado sin servidor."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.api.formatos import colones, decimal, etiqueta, hora_costa_rica, porcentaje


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(750_000, "₡750.000"), (0, "₡0"), (999, "₡999"), (1_234_567, "₡1.234.567"), (-5_000, "-₡5.000")],
)
def prueba_colones_usa_simbolo_y_punto_de_miles(valor: int, esperado: str) -> None:
    assert colones(valor) == esperado


@pytest.mark.parametrize("valor", [None, "750000", 12.5, True])
def prueba_colones_solo_acepta_enteros(valor: object) -> None:
    assert colones(valor) == ""


def prueba_la_fecha_es_dia_mes_anio_en_hora_de_costa_rica() -> None:
    assert hora_costa_rica(datetime(2026, 10, 24, 12, 0, tzinfo=UTC)) == "24/10/2026 06:00"
    # 03:47 UTC del 15 es 21:47 del 14 en Costa Rica (UTC-6).
    assert hora_costa_rica(datetime(2026, 10, 15, 3, 47, tzinfo=UTC)) == "14/10/2026 21:47"
    assert hora_costa_rica("2026-10-24") == ""


def prueba_decimales_y_porcentajes_llevan_coma() -> None:
    assert decimal(11.2, 1) == "11,2"
    assert decimal(16.59) == "16,6"
    assert decimal(3, 0) == "3"
    assert porcentaje(12.5) == "12,5%"
    assert porcentaje(100, 0) == "100%"
    assert porcentaje(None) == ""


@pytest.mark.parametrize(
    ("clave", "esperado"),
    [
        ("nueva", "Nueva"),
        ("en_revision", "En revisión"),
        ("sinpe_movil", "SINPE Móvil"),
        ("sin_asignar", "Sin asignar"),
        ("CRITICA", "Crítica"),
        ("otra_cosa", "Otra cosa"),
        ("", ""),
        (None, ""),
    ],
)
def prueba_etiqueta_traduce_claves_internas(clave: object, esperado: str) -> None:
    assert etiqueta(clave) == esperado
