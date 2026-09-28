"""Conexion a MongoDB.

Este es el UNICO modulo que construye un `AsyncMongoClient` y el unico que toca la
URI. Todo lo demas recibe una `AsyncDatabase` ya conectada. Lo verifica
`pruebas/arquitectura/prueba_capas.py`.

Un solo cliente por proceso, creado en el lifespan de FastAPI e inyectado por
dependencia. El cliente de pymongo ya maneja su propia piscina de conexiones: crear
uno por peticion es el error clasico que agota los sockets del servidor.

Driver: `pymongo.AsyncMongoClient`. NO Motor, que esta deprecado con fin de vida en
mayo de 2026. Por eso el paquete del motor de deteccion se llama `app/deteccion/` y
no `app/motor/`: para que nadie lo confunda con Motor la biblioteca.

Aqui NO hay ninguna consulta, ni siquiera un ping. El ping de `/salud` vive en
`app/repos/salud.py`, porque ninguna consulta va fuera de `app/repos/`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.nucleo.config import Ajustes


def crear_cliente(ajustes: Ajustes) -> AsyncMongoClient:
    """Cliente listo para usar. No conecta todavia.

    `AsyncMongoClient` conecta de forma perezosa, en la primera operacion. Es una
    propiedad que se usa a proposito: el lifespan no hace ping, asi que la API
    arranca aunque el replica set todavia este eligiendo primario, y `/salud` es el
    que responde si la base contesta o no.

    `tz_aware=True` es parte del contrato de fechas: sin eso, pymongo devuelve
    datetimes ingenuos y una fecha que salio como UTC vuelve sin zona, lista para
    que alguien la interprete como hora local.
    """
    return AsyncMongoClient(
        ajustes.mongo.uri,
        serverSelectionTimeoutMS=ajustes.mongo.tiempo_espera_seleccion_ms,
        tz_aware=True,
        appname=f"{ajustes.app.nombre}/{ajustes.app.entorno}",
    )


@asynccontextmanager
async def ciclo_cliente(ajustes: Ajustes) -> AsyncIterator[AsyncMongoClient]:
    """Abre y cierra el cliente. Lo usa el lifespan y las herramientas de linea."""
    cliente = crear_cliente(ajustes)
    try:
        yield cliente
    finally:
        await cliente.close()


def obtener_base(cliente: AsyncMongoClient, ajustes: Ajustes) -> AsyncDatabase:
    """Base de datos del proyecto. El nombre sale de la configuracion, no del codigo."""
    return cliente[ajustes.mongo.base]
