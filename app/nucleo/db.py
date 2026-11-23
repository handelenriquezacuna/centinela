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

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.nucleo.config import Ajustes

# Consistencia explicita (H-09 y T-52).
#
# Donde va: en el CLIENTE y no operacion por operacion. Razon: lo que importa es que
# ninguna escritura del sistema se confirme antes de estar en la mayoria del replica
# set, y ninguna lectura vea datos que un cambio de primario pueda deshacer. Poner el
# concern en cada llamada deja el contrato disperso en 15 repos y un `insert_one` nuevo
# sin el argumento queda sin proteccion sin que nadie lo note. En el cliente lo heredan
# todas las colecciones y tambien la sesion de una transaccion, que es donde se aplica
# el de `escalar` (readConcern al abrirla, writeConcern al confirmarla). Las partes que
# lo necesitan quedan cubiertas sin tocarlas: la alerta y el token de D1
# (`insertar_alerta`, `guardar_token`), las transiciones (`find_one_and_update`) y la
# transaccion de escalar. `pruebas/integracion/prueba_consistencia_mongo.py` lo verifica
# mirando los comandos que viajan al servidor, no la configuracion declarada.
#
# Por que majority/majority y no el valor por omision: desde MongoDB 5.0 el servidor ya
# confirma por mayoria, pero ese valor es del cluster (`setDefaultRWConcern`, un
# arbitro lo cambia) y no del codigo. Aqui se escribe para que el comportamiento no
# dependa de como se haya armado el cluster. Con lectura `majority`, una alerta que el
# portal muestra es una alerta que sobrevive a un failover; con `local` podria
# desaparecer despues de haberla visto. El costo: sin mayoria disponible (2 de 3
# nodos caidos) las escrituras no se confirman, que es lo correcto para un sistema
# que no puede prometer una alerta que podria perderse.
#
# `wtimeout`: sin tope, una escritura sin mayoria espera para siempre. Con 10 s falla
# con error de write concern y D1 la reintenta; el reintento es seguro porque la alerta
# se deduplica por el indice unico de `transaccion_id` (la escritura pudo haberse
# aplicado aunque la confirmacion no llego).
ESCRITURA_W = "majority"
ESCRITURA_TIEMPO_MS = 10_000
LECTURA_NIVEL = "majority"


def crear_cliente(ajustes: Ajustes, *, escuchas: Sequence[Any] = ()) -> AsyncMongoClient:
    """Cliente listo para usar. No conecta todavia.

    `AsyncMongoClient` conecta de forma perezosa, en la primera operacion. Es una
    propiedad que se usa a proposito: el lifespan no hace ping, asi que la API
    arranca aunque el replica set todavia este eligiendo primario, y `/salud` es el
    que responde si la base contesta o no.

    `tz_aware=True` es parte del contrato de fechas: sin eso, pymongo devuelve
    datetimes ingenuos y una fecha que salio como UTC vuelve sin zona, lista para
    que alguien la interprete como hora local.

    `escuchas` son monitores de comandos del driver (`pymongo.monitoring`); lo usan las
    pruebas para ver lo que de verdad viaja al servidor. Produccion no lo pasa.
    """
    return AsyncMongoClient(
        ajustes.mongo.uri,
        serverSelectionTimeoutMS=ajustes.mongo.tiempo_espera_seleccion_ms,
        tz_aware=True,
        w=ESCRITURA_W,
        wTimeoutMS=ESCRITURA_TIEMPO_MS,
        readConcernLevel=LECTURA_NIVEL,
        event_listeners=list(escuchas),
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
