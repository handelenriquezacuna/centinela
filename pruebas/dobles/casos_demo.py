"""Alertas de ejemplo para la vista por casos (T-56) y el doble del repositorio de casos.

`alerta_de_caso` arma un documento valido (v1, como las del demo) con lo que cambia entre
un caso y otro: destino, cliente, puntaje, estado y minuto de creacion. El doble
`RepoCasosFalso` usa `agrupar_por_destino`, la misma funcion pura que la prueba `mongo`
compara contra el `$group` real.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.modelos import (
    ZONA_COSTA_RICA,
    AccionHistorial,
    Alerta,
    EstadoAlerta,
    Severidad,
)
from app.repos.casos_destino import CasoDestino, agrupar_por_destino

INICIO = datetime(2026, 9, 24, 22, 0, tzinfo=ZONA_COSTA_RICA)
MULA = "6033-9001"


def _severidad(puntaje: int) -> Severidad:
    return Severidad.CRITICA if puntaje >= 75 else Severidad.ALTA if puntaje >= 60 else Severidad.MEDIA


def alerta_de_caso(
    numero: int,
    *,
    destino: str = MULA,
    cliente: str | None = None,
    puntaje: int = 75,
    estado: EstadoAlerta = EstadoAlerta.NUEVA,
    minuto: int = 0,
    monto_crc: int = 100_000,
    reglas: tuple[str, ...] = ("REG-001",),
    agente_id: str | None = None,
) -> dict[str, Any]:
    creada = INICIO + timedelta(minutes=minuto)
    return Alerta(
        _id=f"ALR-{numero:03d}",
        esquema_version=1,
        transaccion_id=f"TXN-{numero:03d}",
        reglas_disparadas=list(reglas),
        puntaje=puntaje,
        severidad=_severidad(puntaje),
        estado=estado,
        agente_id=agente_id,
        fecha_creacion=creada,
        cuenta_origen=f"{1000 + numero % 9000:04d}-4455",
        cuenta_destino=destino,
        monto_crc=monto_crc,
        cliente_nombre=cliente or f"Cliente {numero}",
        historial=[AccionHistorial(accion=EstadoAlerta.NUEVA, fecha=creada, nota="creada")],
    ).a_documento()


def caso_de_la_mula() -> list[dict[str, Any]]:
    """Tres victimas en 12 minutos hacia la mula, mas una alerta ya cerrada y otro destino."""
    return [
        alerta_de_caso(101, minuto=0, monto_crc=280_000, reglas=("REG-001", "REG-002")),
        alerta_de_caso(102, minuto=5, puntaje=60, monto_crc=150_000, reglas=("REG-001",)),
        alerta_de_caso(103, minuto=12, puntaje=60, monto_crc=90_000, reglas=("REG-001", "REG-003")),
        alerta_de_caso(104, minuto=3, estado=EstadoAlerta.CONFIRMADA, agente_id="AGT-001"),
        alerta_de_caso(105, destino="3390-6612", puntaje=40, minuto=1, monto_crc=48_300),
    ]


class RepoCasosFalso:
    """`ProtocoloRepoCasosDestino` en memoria, sobre los documentos de un `RepoAlertasFalso`."""

    def __init__(self, fuente: Any, en_riesgo: frozenset[str] = frozenset()) -> None:
        self._fuente = fuente
        self._en_riesgo = en_riesgo
        self.llamadas: list[dict[str, Any]] = []

    def _casos(self) -> list[CasoDestino]:
        return agrupar_por_destino(self._fuente._documentos, en_riesgo=self._en_riesgo)

    async def listar(
        self, *, limite: int, desplazamiento: int = 0
    ) -> tuple[list[CasoDestino], int]:
        self.llamadas.append({"limite": limite, "desplazamiento": desplazamiento})
        casos = self._casos()
        return casos[desplazamiento : desplazamiento + limite], len(casos)

    async def obtener(self, cuenta: str) -> CasoDestino | None:
        return next((c for c in self._casos() if c.cuenta_destino == cuenta), None)
