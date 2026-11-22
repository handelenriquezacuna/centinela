"""Doble de `RepoDetalle` (H-18) y las alertas que necesita su prueba, en memoria."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

AGENTES = [
    {"_id": "AGT-001", "nombre": "Jose Solis", "rol": "agente", "activo": True},
    {"_id": "AGT-002", "nombre": "Laura Mendez", "rol": "agente", "activo": True},
]

TRANSACCION = {
    "_id": "TXN-001",
    "cuenta_origen": "CTA-001",
    "cuenta_destino": "6033-9001",
    "monto_crc": 750_000,
    "moneda": "CRC",
    "canal": "SINPE_MOVIL",
    "fecha": datetime(2026, 10, 15, 4, 47, tzinfo=UTC),
}
CLIENTE = {"_id": "CLI-001", "nombre": "Maria Rodriguez Vargas", "cedula": "1-1111-1111"}
PERFIL = {
    "_id": "CLI-001",
    "promedio_crc": 45_200,
    "maximo_crc": 180_000,
    "num_transferencias": 47,
    "horario_habitual": {"inicio": "07:00", "fin": "19:00"},
    "destinos_frecuentes": ["7001-0001", "7001-0002"],
}


def alerta_v2(estado: str = "nueva", agente_id: str | None = None) -> dict[str, Any]:
    """ALR-001 como la deja la migracion: 3 reglas embebidas que suman 75 y la politica."""
    historial = [
        {
            "accion": "nueva",
            "agente_id": None,
            "fecha": datetime(2026, 10, 15, 4, 47, tzinfo=UTC),
            "nota": "Alerta creada por el motor de deteccion",
        }
    ]
    documento = {
        "_id": "ALR-001",
        "esquema_version": 2,
        "transaccion_id": "TXN-001",
        "reglas_disparadas": [
            {
                "regla_id": "REG-001",
                "codigo": "R-01",
                "version": 1,
                "peso": 35,
                "explicacion": "monto 750000 supera 10 veces el promedio 45200",
            },
            {
                "regla_id": "REG-002",
                "codigo": "R-03",
                "version": 1,
                "peso": 25,
                "explicacion": "destino 6033-9001 sin transferencias previas",
            },
            {
                "regla_id": "REG-003",
                "codigo": "R-05",
                "version": 2,
                "peso": 15,
                "explicacion": "no disponible: regla editada",
            },
        ],
        "politica": {"politica_id": "POL-001", "version": 1},
        "puntaje": 75,
        "severidad": "CRITICA",
        "estado": estado,
        "fecha_creacion": datetime(2026, 10, 15, 4, 47, tzinfo=UTC),
        "historial": historial,
        "cuenta_origen": "8712-4455",
        "cuenta_destino": "6033-9001",
        "monto_crc": 750_000,
        "moneda": "CRC",
        "cliente_nombre": "Maria Rodriguez Vargas",
    }
    # Como en Mongo: una alerta `nueva` no tiene la clave, no la tiene en nulo.
    if agente_id is not None:
        documento["agente_id"] = agente_id
    return documento


def alerta_v1(estado: str = "nueva", agente_id: str | None = None) -> dict[str, Any]:
    documento = alerta_v2(estado, agente_id)
    documento["esquema_version"] = 1
    documento["reglas_disparadas"] = ["REG-001", "REG-002", "REG-003"]
    del documento["politica"]
    return documento


class RepoDetalleFalso:
    """Cumple `ProtocoloRepoDetalle`. `casos` se comparte con `RepoAlertasFalso.casos`."""

    def __init__(self, casos: dict[str, dict[str, Any]] | None = None) -> None:
        self.casos = casos if casos is not None else {}

    async def contexto(self, transaccion_id: str) -> dict[str, Any]:
        if transaccion_id != TRANSACCION["_id"]:
            return {"transaccion": None, "cliente": None, "perfil": None}
        return {
            "transaccion": copy.deepcopy(TRANSACCION),
            "cliente": copy.deepcopy(CLIENTE),
            "perfil": copy.deepcopy(PERFIL),
        }

    async def agentes(self) -> list[dict[str, Any]]:
        return copy.deepcopy(AGENTES)

    async def caso_de_alerta(self, alerta_id: str) -> dict[str, Any] | None:
        return next((c for c in self.casos.values() if alerta_id in c["alertas"]), None)
