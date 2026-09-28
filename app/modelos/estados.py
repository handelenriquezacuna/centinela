"""Maquina de estados de la alerta y escala de severidad.

Este archivo es el contrato compartido mas delicado del sistema: H-14 (resolver una
alerta) y el portal dependen de esta tabla. Solo el arquitecto la cambia, y cambiarla
significa cambiar `TRANSICIONES` y su prueba en el mismo commit.

    nueva -> en_revision -> confirmada
                         -> descartada
                         -> escalada

`confirmada`, `descartada` y `escalada` son terminales: una alerta resuelta no
vuelve a moverse. Reabrir un caso es crear un caso (coleccion `casos`), no
retroceder la alerta, para que el historial embebido no mienta.

Lectura estricta a proposito: NO existe el salto `nueva -> confirmada`. Un agente
toma la alerta (`en_revision`) antes de resolverla, y asi el historial siempre
registra quien la tomo. `docs/01-vision-y-alcance.md` hablaba de un estado
`resuelta` que nunca existio; manda esta tabla.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class EstadoAlerta(StrEnum):
    """Estados posibles de una alerta. El valor es lo que se guarda en Mongo."""

    NUEVA = "nueva"
    EN_REVISION = "en_revision"
    CONFIRMADA = "confirmada"
    DESCARTADA = "descartada"
    ESCALADA = "escalada"


class Severidad(StrEnum):
    """Severidad de la alerta, en mayuscula como en indicadores_diarios.por_severidad.

    No hay BAJA: si el puntaje no supera el umbral no se crea alerta (UC-02), asi
    que una alerta de severidad baja no existe. Los cortes de puntaje que deciden
    cual de las tres aplica viven en Mongo (politica_deteccion), nunca aqui.
    """

    CRITICA = "CRITICA"
    ALTA = "ALTA"
    MEDIA = "MEDIA"


ESTADO_INICIAL = EstadoAlerta.NUEVA

# La tabla. Todo estado aparece como clave, incluidos los terminales con conjunto
# vacio, para que agregar un estado nuevo sin decidir sus salidas no compile.
TRANSICIONES: Mapping[EstadoAlerta, frozenset[EstadoAlerta]] = {
    EstadoAlerta.NUEVA: frozenset({EstadoAlerta.EN_REVISION}),
    EstadoAlerta.EN_REVISION: frozenset(
        {
            EstadoAlerta.CONFIRMADA,
            EstadoAlerta.DESCARTADA,
            EstadoAlerta.ESCALADA,
        }
    ),
    EstadoAlerta.CONFIRMADA: frozenset(),
    EstadoAlerta.DESCARTADA: frozenset(),
    EstadoAlerta.ESCALADA: frozenset(),
}

ESTADOS_TERMINALES = frozenset(
    estado for estado, salidas in TRANSICIONES.items() if not salidas
)

ESTADOS_ABIERTOS = frozenset(EstadoAlerta) - ESTADOS_TERMINALES


class TransicionInvalida(ValueError):
    """Se intento mover una alerta por un camino que no esta en la tabla."""

    def __init__(self, origen: EstadoAlerta, destino: EstadoAlerta) -> None:
        self.origen = origen
        self.destino = destino
        permitidos = sorted(TRANSICIONES.get(origen, frozenset()))
        legibles = ", ".join(permitidos) if permitidos else "ninguno (estado terminal)"
        super().__init__(
            f"transicion invalida: {origen} -> {destino}. "
            f"Desde {origen} solo se permite: {legibles}"
        )


def transiciones_validas(origen: EstadoAlerta) -> frozenset[EstadoAlerta]:
    """Destinos permitidos desde `origen`. El portal la usa para pintar botones."""
    return TRANSICIONES[EstadoAlerta(origen)]


def transicion_permitida(origen: EstadoAlerta, destino: EstadoAlerta) -> bool:
    return EstadoAlerta(destino) in transiciones_validas(origen)


def exigir_transicion(origen: EstadoAlerta, destino: EstadoAlerta) -> None:
    """Lanza `TransicionInvalida` si el movimiento no esta en la tabla.

    Esta es la funcion que H-14 tiene que llamar antes de escribir el nuevo estado.
    """
    if not transicion_permitida(origen, destino):
        raise TransicionInvalida(EstadoAlerta(origen), EstadoAlerta(destino))


def es_terminal(estado: EstadoAlerta) -> bool:
    return EstadoAlerta(estado) in ESTADOS_TERMINALES
