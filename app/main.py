"""Punto de entrada del monolito: `uvicorn app.main:app`.

Un solo proceso sirve la API, el portal y, desde H-00B, los disparadores de deteccion
como tareas del lifespan. Un solo worker, porque la deteccion tiene que estar activa
exactamente una vez: dos workers escuchando el mismo change stream crean la alerta dos
veces.

El lifespan crea el unico cliente de Mongo del proceso y lo cierra al salir. NO hace
ping: `AsyncMongoClient` conecta de forma perezosa, asi que la API arranca aunque el
replica set todavia este eligiendo primario, y es `/salud` quien informa si la base
responde.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import VERSION
from app.api.errores import registrar_manejadores
from app.api.rutas import enrutador_v1
from app.api.rutas import salud as rutas_salud
from app.nucleo.config import Ajustes, obtener_ajustes
from app.nucleo.db import crear_cliente

DESCRIPCION = """
Monitoreo de fraude bancario en tiempo real sobre transferencias SINPE Movil
originadas por vishing.

**Contratos de datos** (documento completo en `docs/06-plataforma.md`):

- Dinero: `monto_crc`, entero de 64 bits, colones enteros. Nunca flotante.
- Fechas: ISODate en UTC. El portal formatea a hora de Costa Rica al presentar.
  Unica excepcion: `indicadores_diarios.fecha`, texto `YYYY-MM-DD`.
- Identificadores: cadenas legibles (`CLI-001`, `TXN-001`), no ObjectId.
- Todo documento lleva `esquema_version`.
- Estados de la alerta: `nueva` -> `en_revision` -> `confirmada` / `descartada` / `escalada`.

**Errores**: todos comparten la misma forma, `{"error": {"codigo", "mensaje", "detalles"}}`.
"""


@asynccontextmanager
async def ciclo_vida(aplicacion: FastAPI) -> AsyncIterator[None]:
    """Abre el cliente de Mongo al arrancar y lo cierra al terminar.

    Ojo en pruebas: `httpx.AsyncClient` NO dispara el lifespan por su cuenta, y el
    cliente de Mongo se crea justamente aqui. Hay que envolver la aplicacion con
    `LifespanManager` de `asgi-lifespan`, como hace `pruebas/conftest.py`.
    """
    ajustes: Ajustes = obtener_ajustes()
    logging.basicConfig(
        level=ajustes.registro.nivel,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    registro = logging.getLogger("centinela")

    cliente = crear_cliente(ajustes)
    aplicacion.state.ajustes = ajustes
    aplicacion.state.cliente_mongo = cliente
    registro.info(
        "Centinela %s arriba | entorno=%s base=%s",
        VERSION,
        ajustes.app.entorno,
        ajustes.mongo.base,
    )

    try:
        yield
    finally:
        await cliente.close()
        registro.info("Centinela abajo, cliente de Mongo cerrado")


def crear_aplicacion() -> FastAPI:
    """Construye la aplicacion. Es una funcion para poder armarla en una prueba."""
    aplicacion = FastAPI(
        title="Centinela",
        version=VERSION,
        description=DESCRIPCION,
        summary="Monitoreo de fraude SINPE en tiempo real",
        lifespan=ciclo_vida,
    )

    registrar_manejadores(aplicacion)
    aplicacion.include_router(rutas_salud.enrutador)
    aplicacion.include_router(enrutador_v1)

    return aplicacion


app = crear_aplicacion()
