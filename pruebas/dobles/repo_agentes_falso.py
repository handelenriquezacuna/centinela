"""Doble de `RepoAgentes` (H-25): los agentes en memoria, con credenciales ya cifradas."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from app.seguridad import contrasenas
from app.seguridad.bloqueo import ResultadoFallo, calcular_fallo

# Costo bajo: las pruebas verifican el mecanismo y hacen muchos inicios de sesion.
PARAMETROS_BARATOS = {"algoritmo": "scrypt", "n": 2**10, "r": 8, "p": 1, "dklen": 32}

CONTRASENA = "contrasena-de-pruebas-1"


def agentes_de_prueba() -> list[dict[str, Any]]:
    credencial = contrasenas.cifrar(CONTRASENA, PARAMETROS_BARATOS)
    return [
        {"_id": "AGT-001", "nombre": "Jose Solis", "rol": "agente", "activo": True,
         "credencial": credencial},
        {"_id": "AGT-002", "nombre": "Laura Mendez", "rol": "supervisor", "activo": True,
         "credencial": credencial},
        {"_id": "AGT-003", "nombre": "Admin", "rol": "administrador", "activo": True,
         "credencial": credencial},
        {"_id": "AGT-004", "nombre": "Baja", "rol": "agente", "activo": False,
         "credencial": credencial},
        {"_id": "AGT-005", "nombre": "Sin credencial", "rol": "agente", "activo": True},
    ]


class RepoAgentesFalso:
    def __init__(self, agentes: list[dict[str, Any]] | None = None) -> None:
        self.agentes = {a["_id"]: a for a in (agentes or agentes_de_prueba())}

    async def obtener(self, agente_id: str) -> dict[str, Any] | None:
        encontrado = self.agentes.get(agente_id)
        return copy.deepcopy(encontrado) if encontrado is not None else None

    async def guardar_credencial(
        self, agente_id: str, credencial: dict[str, Any], rol: str | None = None
    ) -> bool:
        if agente_id not in self.agentes:
            return False
        self.agentes[agente_id]["credencial"] = credencial
        if rol is not None:
            self.agentes[agente_id]["rol"] = rol
        return True

    async def registrar_fallo(
        self, agente_id: str, *, maximo: int, bloqueo_minutos: int, ahora: datetime
    ) -> ResultadoFallo | None:
        agente = self.agentes.get(agente_id)
        if agente is None:
            return None
        resultado = calcular_fallo(
            agente, maximo=maximo, bloqueo_minutos=bloqueo_minutos, ahora=ahora
        )
        agente["intentos_fallidos"] = resultado.intentos_fallidos
        if resultado.bloqueado_hasta is not None:
            agente["bloqueado_hasta"] = resultado.bloqueado_hasta
        else:
            agente.pop("bloqueado_hasta", None)
        return resultado

    async def reiniciar_intentos(self, agente_id: str) -> bool:
        agente = self.agentes.get(agente_id)
        if agente is None or not ({"intentos_fallidos", "bloqueado_hasta"} & agente.keys()):
            return False
        agente.pop("intentos_fallidos", None)
        agente.pop("bloqueado_hasta", None)
        return True

    async def desbloquear(self, agente_id: str) -> bool:
        if agente_id not in self.agentes:
            return False
        await self.reiniciar_intentos(agente_id)
        return True

    async def listar(self) -> list[dict[str, Any]]:
        return [
            {
                **{k: v for k, v in agente.items() if k != "credencial"},
                "tiene_credencial": "credencial" in agente,
            }
            for _, agente in sorted(self.agentes.items())
        ]
