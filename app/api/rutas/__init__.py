"""Routers de la API.

`enrutador_v1` monta lo que va bajo `/api/v1`. Las dos rutas de operacion (`/salud` y
`/estado`) NO van versionadas: no son parte del contrato del portal, son la forma de
preguntarle al proceso como esta, y un orquestador no debe tener que saber una version
de API para hacerlo.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import PREFIJO_V1
from app.api.rutas import alertas, sse

enrutador_v1 = APIRouter(prefix=PREFIJO_V1)
enrutador_v1.include_router(alertas.enrutador)
enrutador_v1.include_router(sse.enrutador)

__all__ = ["enrutador_v1"]
