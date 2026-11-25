"""Routers de la API.

`enrutador_v1` monta lo que va bajo `/api/v1`. Las dos rutas de operacion (`/salud` y
`/estado`) NO van versionadas: no son parte del contrato del portal, son la forma de
preguntarle al proceso como esta, y un orquestador no debe tener que saber una version
de API para hacerlo.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api import PREFIJO_V1
from app.api.errores import respuestas_documentadas
from app.api.rutas import (
    acceso,
    alertas,
    inteligencia,
    lecturas,
    listas_riesgo,
    reglas,
    respaldos,
    sse,
    transacciones,
)

enrutador_v1 = APIRouter(
    prefix=PREFIJO_V1,
    responses=respuestas_documentadas(
        status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN
    ),
)
enrutador_v1.include_router(acceso.enrutador_api)
enrutador_v1.include_router(alertas.enrutador)
enrutador_v1.include_router(sse.enrutador)
enrutador_v1.include_router(reglas.enrutador)
enrutador_v1.include_router(inteligencia.enrutador)
enrutador_v1.include_router(lecturas.enrutador)
enrutador_v1.include_router(listas_riesgo.enrutador)
enrutador_v1.include_router(transacciones.enrutador)
enrutador_v1.include_router(respaldos.enrutador)

__all__ = ["enrutador_v1"]
