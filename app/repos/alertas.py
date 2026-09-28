"""Consultas sobre `alertas`.

`construir_filtro` esta separada de la consulta a proposito: es una funcion pura, se
prueba sin base de datos (`pruebas/unitarias/prueba_filtros_alertas.py`) y es donde
de verdad se puede meter un error silencioso.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.modelos import COLECCION_ALERTAS, EstadoAlerta, Severidad

# Orden del panel: lo mas reciente primero, y el identificador como desempate. Sin el
# desempate, dos alertas con la misma marca de tiempo pueden salir en distinto orden
# entre paginas y el agente ve un documento dos veces o ninguna.
ORDEN_PANEL: list[tuple[str, int]] = [
    ("fecha_creacion", DESCENDING),
    ("_id", ASCENDING),
]


def construir_filtro(
    estado: EstadoAlerta | None = None,
    severidad: Severidad | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
) -> dict[str, Any]:
    """Filtro de Mongo a partir de los parametros del panel.

    Los filtros son combinables: cada uno que llega agrega una condicion. Ninguno
    que llega vacio agrega nada, para que la consulta sin filtros siga pudiendo usar
    el indice del panel.

    El rango de fecha se arma sobre `fecha_creacion` y es cerrado por abajo y
    abierto por arriba (`$gte` y `$lte` es lo intuitivo para un agente que pide "del
    1 al 15"; se usa `$lte` a proposito).
    """
    filtro: dict[str, Any] = {}

    if estado is not None:
        filtro["estado"] = EstadoAlerta(estado).value
    if severidad is not None:
        filtro["severidad"] = Severidad(severidad).value

    rango: dict[str, datetime] = {}
    if desde is not None:
        rango["$gte"] = desde
    if hasta is not None:
        rango["$lte"] = hasta
    if rango:
        filtro["fecha_creacion"] = rango

    return filtro


@runtime_checkable
class ProtocoloRepoAlertas(Protocol):
    """Lo que la API necesita de este repositorio.

    Existe para que las pruebas de contrato de la API puedan inyectar un doble con
    `dependency_overrides` y correr sin Mongo arriba. Si el metodo cambia de forma,
    el doble deja de cumplir el protocolo y la prueba lo dice.
    """

    async def listar(
        self,
        *,
        estado: EstadoAlerta | None,
        severidad: Severidad | None,
        desde: datetime | None,
        hasta: datetime | None,
        limite: int,
        desplazamiento: int,
    ) -> tuple[list[dict[str, Any]], int]: ...


class RepoAlertas:
    """Acceso a la coleccion `alertas`."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._coleccion = base[COLECCION_ALERTAS]

    async def listar(
        self,
        *,
        estado: EstadoAlerta | None = None,
        severidad: Severidad | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 50,
        desplazamiento: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Pagina de alertas y total que cumple el filtro.

        El total se cuenta con el mismo filtro y no con el de la pagina: el agente
        necesita saber cuantas alertas hay en su cola, no cuantas caben en la
        pantalla.
        """
        filtro = construir_filtro(
            estado=estado, severidad=severidad, desde=desde, hasta=hasta
        )

        total = await self._coleccion.count_documents(filtro)

        cursor = (
            self._coleccion.find(filtro)
            .sort(ORDEN_PANEL)
            .skip(desplazamiento)
            .limit(limite)
        )
        documentos = await cursor.to_list(length=limite)

        return documentos, total
