"""Contrato de la maquina de estados de la alerta.

    nueva -> en_revision -> confirmada / descartada / escalada

La prueba central no es la de los caminos validos: es la que recorre TODOS los pares
posibles de estados y exige que los que no estan en la tabla sean rechazados. Con
cinco estados son 25 pares, de los cuales solo 4 son validos. Probar solo los 4
validos deja pasar cualquier transicion que alguien agregue por descuido.
"""

from __future__ import annotations

from itertools import product

import pytest

from app.modelos.estados import (
    ESTADO_INICIAL,
    ESTADOS_ABIERTOS,
    ESTADOS_TERMINALES,
    TRANSICIONES,
    EstadoAlerta,
    Severidad,
    TransicionInvalida,
    es_terminal,
    exigir_transicion,
    transicion_permitida,
    transiciones_validas,
)

# La tabla, escrita a mano y aparte de la implementacion a proposito: si alguien
# cambia TRANSICIONES en app/, esta lista NO cambia con el, y la prueba falla. Una
# prueba que importa la tabla que quiere verificar no verifica nada.
CAMINOS_VALIDOS = {
    (EstadoAlerta.NUEVA, EstadoAlerta.EN_REVISION),
    (EstadoAlerta.EN_REVISION, EstadoAlerta.CONFIRMADA),
    (EstadoAlerta.EN_REVISION, EstadoAlerta.DESCARTADA),
    (EstadoAlerta.EN_REVISION, EstadoAlerta.ESCALADA),
}


def prueba_los_cinco_estados_y_ni_uno_mas() -> None:
    """El vocabulario de estados es cerrado y en minuscula, como en Mongo."""
    assert {estado.value for estado in EstadoAlerta} == {
        "nueva",
        "en_revision",
        "confirmada",
        "descartada",
        "escalada",
    }


def prueba_las_tres_severidades_y_ni_una_mas() -> None:
    """No existe severidad BAJA: bajo umbral no se crea alerta (UC-02)."""
    assert {severidad.value for severidad in Severidad} == {"CRITICA", "ALTA", "MEDIA"}


@pytest.mark.parametrize(("origen", "destino"), sorted(CAMINOS_VALIDOS))
def prueba_los_caminos_validos_pasan(
    origen: EstadoAlerta, destino: EstadoAlerta
) -> None:
    assert transicion_permitida(origen, destino)
    exigir_transicion(origen, destino)  # no lanza


@pytest.mark.parametrize(
    ("origen", "destino"),
    sorted(
        par
        for par in product(EstadoAlerta, repeat=2)
        if par not in CAMINOS_VALIDOS
    ),
)
def prueba_todo_lo_demas_se_rechaza(
    origen: EstadoAlerta, destino: EstadoAlerta
) -> None:
    """Los 21 pares que no estan en la tabla, uno por uno.

    Incluye los casos que mas facil se cuelan: el salto directo
    `nueva -> confirmada` (resolver sin tomar la alerta), volver de un estado
    terminal, y quedarse en el mismo estado.
    """
    assert not transicion_permitida(origen, destino)

    with pytest.raises(TransicionInvalida) as fallo:
        exigir_transicion(origen, destino)

    assert fallo.value.origen == origen
    assert fallo.value.destino == destino
    # El mensaje tiene que decir que si se puede: es lo que lee el agente.
    assert str(origen) in str(fallo.value)


def prueba_no_se_puede_resolver_sin_tomar_la_alerta() -> None:
    """El caso concreto que la tabla estricta previene.

    Si `nueva -> confirmada` fuera valido, el historial embebido no tendria quien
    tomo la alerta, y el rastro de auditoria dejaria de responder "quien decidio".
    """
    for salida in (
        EstadoAlerta.CONFIRMADA,
        EstadoAlerta.DESCARTADA,
        EstadoAlerta.ESCALADA,
    ):
        assert not transicion_permitida(EstadoAlerta.NUEVA, salida)


def prueba_los_estados_terminales_no_tienen_salida() -> None:
    assert ESTADOS_TERMINALES == {
        EstadoAlerta.CONFIRMADA,
        EstadoAlerta.DESCARTADA,
        EstadoAlerta.ESCALADA,
    }
    for estado in ESTADOS_TERMINALES:
        assert transiciones_validas(estado) == frozenset()
        assert es_terminal(estado)


def prueba_los_estados_abiertos_son_los_otros_dos() -> None:
    assert ESTADOS_ABIERTOS == {EstadoAlerta.NUEVA, EstadoAlerta.EN_REVISION}
    assert ESTADO_INICIAL == EstadoAlerta.NUEVA


def prueba_todo_estado_esta_en_la_tabla() -> None:
    """Agregar un estado sin decidir sus salidas tiene que romper algo.

    Sin esta prueba, un estado nuevo sin entrada en TRANSICIONES haria que
    `transiciones_validas` lance KeyError en produccion en vez de aqui.
    """
    assert set(TRANSICIONES) == set(EstadoAlerta)


def prueba_se_aceptan_cadenas_ademas_de_enums() -> None:
    """El estado llega como texto desde Mongo y desde la URL; tiene que servir igual."""
    assert transicion_permitida("nueva", "en_revision")
    with pytest.raises(TransicionInvalida):
        exigir_transicion("nueva", "confirmada")
