"""Punto de entrada del monolito: `uvicorn app.main:app`.

Un solo proceso sirve la API, el portal y las tareas de fondo como `asyncio.Task` del
lifespan. Un solo worker, porque la deteccion tiene que estar activa exactamente una
vez: dos workers escuchando el mismo change stream crean la alerta dos veces.

El lifespan hace tres cosas y en este orden:

1. Crea el unico cliente de Mongo del proceso. NO hace ping: `AsyncMongoClient`
   conecta de forma perezosa, asi que la API arranca aunque el replica set todavia
   este eligiendo primario, y es `/salud` quien informa si la base responde.
2. Crea el supervisor y lanza las tareas de fondo. Hoy es una, el latido; cuando
   existan, D1 (H-09), D2 (H-11) y D3 (H-22) se lanzan aqui mismo con una linea cada
   uno y `/estado` los vigila sin cambios.
3. Al salir, detiene las tareas ANTES de cerrar el cliente. El orden importa: si el
   cliente se cerrara primero, las tareas que estan en medio de una consulta
   reventarian al apagar y el registro se llenaria de errores que no significan nada.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import VERSION
from app.api.errores import registrar_manejadores
from app.api.rutas import enrutador_v1
from app.api.rutas import estado as rutas_estado
from app.api.rutas import salud as rutas_salud
from app.nucleo.config import Ajustes, obtener_ajustes
from app.nucleo.db import crear_cliente, obtener_base
from app.repos.salud import exigir_conexion
from app.supervision import NOMBRE_LATIDO, Supervisor, latido

# app/main.py -> app/estaticos. Aqui vive `htmx-ext-sse.js`, vendorizado: la extension
# SSE no viene en el nucleo de htmx y el panel no puede depender de una CDN para
# funcionar el dia de la defensa.
RUTA_ESTATICOS = Path(__file__).resolve().parent / "estaticos"

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

    supervisor = Supervisor(
        tolerancia_segundos=ajustes.supervision.tolerancia_retraso_segundos
    )
    aplicacion.state.supervisor = supervisor

    # El latido es la unica tarea de fondo de H-00B, y es real: comprueba que la base
    # responde sin que nadie pregunte. Aca es donde se van a lanzar los disparadores
    # cuando existan, con una linea cada uno:
    #
    #   supervisor.lanzar("d1_deteccion", d1(base, supervisor))   # H-09
    #   supervisor.lanzar("d2_notificacion", d2(base, supervisor))  # H-11
    #
    # `partial` y no una lambda: la comprobacion se pasa como funcion sin argumentos
    # para que `app/supervision/` no tenga que importar el driver.
    base = obtener_base(cliente, ajustes)
    supervisor.lanzar(
        NOMBRE_LATIDO,
        latido(
            partial(exigir_conexion, base),
            supervisor,
            intervalo_segundos=ajustes.supervision.intervalo_latido_segundos,
        ),
    )

    registro.info(
        "Centinela %s arriba | entorno=%s base=%s",
        VERSION,
        ajustes.app.entorno,
        ajustes.mongo.base,
    )

    try:
        yield
    finally:
        # Primero las tareas, despues el cliente: al reves, una tarea a medio camino se
        # encuentra el cliente cerrado debajo y revienta al apagar.
        await supervisor.detener()
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
    aplicacion.include_router(rutas_estado.enrutador)
    aplicacion.include_router(enrutador_v1)

    # Los estaticos se sirven desde el propio proceso: es un monolito y no hay un
    # servidor web delante. `check_dir=False` no se usa a proposito -si la carpeta
    # desaparece, la aplicacion no arranca en vez de servir 404 en silencio.
    aplicacion.mount(
        "/estaticos", StaticFiles(directory=RUTA_ESTATICOS), name="estaticos"
    )

    return aplicacion


app = crear_aplicacion()
