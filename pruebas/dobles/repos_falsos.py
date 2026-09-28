"""Dobles de los repositorios, en memoria.

Sirven para que las pruebas de contrato de la API corran sin Mongo arriba. El doble
implementa `ProtocoloRepoAlertas` de forma estructural: si la firma del repositorio
real cambia, `prueba_el_doble_cumple_el_protocolo` lo dice.

El doble filtra en Python lo mismo que el repositorio real delega a Mongo. Es una
duplicacion consciente y acotada: si los dos se separan, la prueba marcada `mongo`
compara los dos caminos sobre los mismos datos y el desacuerdo sale a la luz.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.modelos import EstadoAlerta, Severidad
from app.modelos.base import asumir_utc


class RepoAlertasFalso:
    """Repositorio de alertas en memoria."""

    def __init__(self, documentos: list[dict[str, Any]]) -> None:
        # Copia: una prueba que modifique un documento no debe ensuciar a la que sigue.
        self._documentos = [dict(documento) for documento in documentos]
        self.llamadas: list[dict[str, Any]] = []

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
        # Se registra lo que llego para poder afirmar que la ruta aplico el acotado
        # del limite ANTES de consultar, y no despues de traer todo.
        self.llamadas.append(
            {
                "estado": estado,
                "severidad": severidad,
                "desde": desde,
                "hasta": hasta,
                "limite": limite,
                "desplazamiento": desplazamiento,
            }
        )

        filtrados = [
            documento
            for documento in self._documentos
            if self._cumple(documento, estado, severidad, desde, hasta)
        ]

        # Mismo orden que ORDEN_PANEL del repositorio real: fecha_creacion
        # descendente, _id ascendente como desempate.
        filtrados.sort(key=lambda documento: documento["_id"])
        filtrados.sort(key=lambda documento: documento["fecha_creacion"], reverse=True)

        total = len(filtrados)
        pagina = filtrados[desplazamiento : desplazamiento + limite]
        return pagina, total

    @staticmethod
    def _cumple(
        documento: dict[str, Any],
        estado: EstadoAlerta | None,
        severidad: Severidad | None,
        desde: datetime | None,
        hasta: datetime | None,
    ) -> bool:
        if estado is not None and documento["estado"] != EstadoAlerta(estado).value:
            return False
        if severidad is not None and documento["severidad"] != Severidad(severidad).value:
            return False

        fecha = asumir_utc(documento["fecha_creacion"])
        if desde is not None and fecha < asumir_utc(desde):
            return False
        if hasta is not None and fecha > asumir_utc(hasta):  # noqa: SIM103 - guardas uniformes
            return False
        return True
