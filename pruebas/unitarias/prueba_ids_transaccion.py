"""T-69 · Contrato de ids de transaccion y filtro del rezago, sin base de datos.

Tres familias de ids conviven: la demo sembrada (`TXN-001`..`TXN-007`, tres digitos), el
generador y la cadena en vivo (`TXN-NNNNNN`, seis digitos). El rezago de D1 compara `_id`
como texto: estas pruebas fijan que solo cuentan los de seis digitos y que el asignador
comun siempre da el siguiente al mayor.
"""

from __future__ import annotations

import re

import pytest

from app.repos.deteccion import filtro_pendientes
from app.repos.ids_transaccion import (
    ID_MAXIMO,
    ID_MINIMO,
    es_id_de_seis_digitos,
    id_transaccion,
    numero_de,
    numero_siguiente,
)

# -- Asignador --------------------------------------------------------------


@pytest.mark.parametrize(
    ("mayor", "esperado"),
    [
        (None, 1),  # base vacia
        ("TXN-007", 1),  # solo la demo de tres digitos: no cuenta
        ("TXN-000001", 2),
        ("TXN-000600", 601),
        ("TXN-900010", 900_011),  # lo que dejo la cadena vieja: se continua desde ahi
        ("TXN-999998", 999_999),
    ],
)
def prueba_el_siguiente_es_el_mayor_de_seis_digitos_mas_uno(mayor: str | None, esperado: int) -> None:
    assert numero_siguiente(mayor) == esperado


def prueba_al_agotar_el_rango_se_detiene_en_vez_de_pasar_a_siete_digitos() -> None:
    with pytest.raises(OverflowError, match="TXN-999999"):
        numero_siguiente("TXN-999999")


def prueba_el_id_tiene_siempre_seis_digitos() -> None:
    assert id_transaccion(1) == "TXN-000001"
    assert id_transaccion(900_001) == "TXN-900001"
    with pytest.raises(ValueError):
        id_transaccion(0)
    with pytest.raises(ValueError):
        id_transaccion(1_000_000)


@pytest.mark.parametrize(
    ("texto", "numero"),
    [("TXN-000042", 42), ("TXN-007", None), ("TXN-0000042", None), ("ALR-000042", None), (None, None)],
)
def prueba_solo_los_ids_de_seis_digitos_tienen_numero(texto: str | None, numero: int | None) -> None:
    assert numero_de(texto) == numero
    assert es_id_de_seis_digitos(texto) is (numero is not None)


# -- Filtro del rezago ------------------------------------------------------


def _cuenta(filtro: dict, identificador: str) -> bool:
    """Lo que el servidor decide por clave de indice: rango de texto y patron anclado."""
    condicion = filtro["_id"]
    return (
        condicion["$gt"] < identificador <= condicion["$lte"]
        and re.search(condicion["$regex"], identificador) is not None
    )


def prueba_la_ultima_de_seis_digitos_es_el_limite_inferior() -> None:
    filtro = filtro_pendientes("TXN-000500")["_id"]

    assert (filtro["$gt"], filtro["$lte"]) == ("TXN-000500", ID_MAXIMO)


def prueba_la_ultima_de_tres_digitos_baja_el_limite_a_cero() -> None:
    """Fallo original: D1 proceso TXN-007 y `_id > "TXN-007"` no cuenta nada de TXN-000500."""
    filtro = filtro_pendientes("TXN-007")

    assert (filtro["_id"]["$gt"], filtro["_id"]["$lte"]) == (ID_MINIMO, ID_MAXIMO)
    assert _cuenta(filtro, "TXN-000001") and _cuenta(filtro, "TXN-000500")
    assert _cuenta(filtro, "TXN-999999")


def prueba_en_texto_la_demo_cae_dentro_del_rango_y_por_eso_va_el_patron() -> None:
    demo, generada = "TXN-001", "TXN-000500"
    assert generada < demo <= ID_MAXIMO


@pytest.mark.parametrize("ultima", ["TXN-007", "TXN-000500", "TXN-000005"])
def prueba_la_demo_de_tres_digitos_nunca_cuenta_como_pendiente(ultima: str) -> None:
    filtro = filtro_pendientes(ultima)

    assert not any(_cuenta(filtro, f"TXN-00{n}") for n in range(1, 8))
    assert not _cuenta(filtro, "TXN-0000700"), "tampoco los de siete digitos"


def prueba_el_rango_9nnnnn_es_un_id_mas() -> None:
    """La cadena en vivo ya no usa un rango propio: TXN-900001 cuenta como cualquiera."""
    filtro = filtro_pendientes("TXN-000010")

    assert _cuenta(filtro, "TXN-900001")
    assert not _cuenta(filtro, "TXN-000010")  # la propia ultima ya esta evaluada


def prueba_ya_no_existe_el_rango_9nnnnn_del_demo_en_vivo() -> None:
    from app.repos import demo_cadena

    assert not hasattr(demo_cadena, "PRIMER_NUMERO")
