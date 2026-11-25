"""Punto de entrada del monolito: `uvicorn app.main:app`.

Un solo proceso sirve la API, el portal y las tareas de fondo como `asyncio.Task` del
lifespan. Un solo worker, porque la deteccion tiene que estar activa exactamente una
vez: dos workers escuchando el mismo change stream crean la alerta dos veces.

El lifespan hace tres cosas y en este orden:

1. Crea el unico cliente de Mongo del proceso. NO hace ping: `AsyncMongoClient`
   conecta de forma perezosa, asi que la API arranca aunque el replica set todavia
   este eligiendo primario, y es `/salud` quien informa si la base responde.
2. Crea el supervisor y lanza las tareas de fondo: el latido y D1 (H-09). D2 (H-11)
   y D3 (H-22) se lanzan aqui mismo con una linea cada uno y `/estado` los vigila
   sin cambios.
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

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app import VERSION
from app.api.errores import registrar_manejadores
from app.api.rutas import acceso as rutas_acceso
from app.api.rutas import enrutador_v1
from app.api.rutas import estado as rutas_estado
from app.api.rutas import paginas as rutas_paginas
from app.api.rutas import paginas_busqueda as rutas_paginas_busqueda
from app.api.rutas import paginas_casos as rutas_paginas_casos
from app.api.rutas import paginas_tablero as rutas_paginas_tablero
from app.api.rutas import salud as rutas_salud
from app.api.rutas import spa as rutas_spa
from app.api.sesion import guardia, registrar_manejadores_sesion
from app.deteccion.d1 import NOMBRE_D1, d1
from app.deteccion.d3 import NOMBRE_D3, d3
from app.nucleo.config import Ajustes, obtener_ajustes
from app.nucleo.db import crear_cliente, obtener_base
from app.repos.deteccion import RepoDeteccion
from app.repos.indicadores import RepoIndicadores
from app.repos.perfiles import RepoPerfiles
from app.repos.rasgos_grafo import RepoRasgosGrafo
from app.repos.respaldos import RepoRespaldos
from app.repos.salud import exigir_conexion
from app.respaldos.servicio import ServicioRespaldos
from app.seguridad.servicio import ServicioSesion
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
    # D-74: el usuario de respaldo (lee `antifraude`, es dueno de la base temporal de
    # verificacion) es OTRO usuario y por tanto otro cliente. Solo existe si
    # `respaldos.uri` esta puesta; sin ella, los respaldos usan el cliente de la app.
    cliente_respaldos = (
        crear_cliente(ajustes, uri=ajustes.respaldos.uri) if ajustes.respaldos.uri else cliente
    )
    aplicacion.state.servicio_respaldos = ServicioRespaldos(
        RepoRespaldos(cliente_respaldos), ajustes
    )

    # H-25: la clave de firma. Sin ella en dev se genera una al vuelo y queda la
    # advertencia en /estado; fuera de dev `Ajustes` ya se nego a arrancar.
    servicio_sesion = ServicioSesion.desde_ajustes(ajustes)
    aplicacion.state.servicio_sesion = servicio_sesion
    for advertencia in servicio_sesion.advertencias:
        registro.warning(advertencia)

    supervisor = Supervisor(
        tolerancia_segundos=ajustes.supervision.tolerancia_retraso_segundos
    )
    aplicacion.state.supervisor = supervisor

    # El latido es la tarea de fondo de H-00B, y es real: comprueba que la base
    # responde sin que nadie pregunte. Aca se lanzan tambien los disparadores, con una
    # linea cada uno:
    #
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
    # H-09: transaccion -> alerta. Recibe el repositorio, no la base (ver app/deteccion/d1.py).
    supervisor.lanzar(NOMBRE_D1, d1(RepoDeteccion(base), supervisor, ajustes=ajustes.deteccion))
    # H-22: recalcula indicadores_diarios del dia actual cada N segundos.
    # T-63: la misma vuelta de D3 recalcula los rasgos de grafo de R-11 (solo con la regla activa).
    supervisor.lanzar(
        NOMBRE_D3,
        d3(
            RepoIndicadores(base),
            supervisor,
            ajustes=ajustes.indicadores,
            repo_rasgos=RepoRasgosGrafo(base, ajustes.rasgos_grafo),
            # T-76: perfiles solo si `perfiles.activo` (cambia lo que deciden R-01 y R-05).
            repo_perfiles=RepoPerfiles(base) if ajustes.perfiles.activo else None,
            ajustes_perfiles=ajustes.perfiles,
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
        if cliente_respaldos is not cliente:
            await cliente_respaldos.close()
        await cliente.close()
        registro.info("Centinela abajo, cliente de Mongo cerrado")


def crear_aplicacion(interfaz: str | None = None) -> FastAPI:
    """Construye la aplicacion. Es una funcion para poder armarla en una prueba.

    `interfaz` es `portal.interfaz` (`htmx` o `spa`, T-61); por omision la de los ajustes.
    Con `spa`, el router de la SPA se registra ANTES que las paginas HTMX: para una misma
    ruta gana el primero registrado, asi que `/panel` devuelve el `index.html` de la SPA y
    los fragmentos HTMX (`/panel/cola`, formularios) siguen vivos mientras dure la
    migracion. La fase 2 retira la rama HTMX.
    """
    # `Ajustes()` y no `obtener_ajustes()`: construir la app no debe memorizar los ajustes
    # que despues lee el lifespan (una prueba cambia el entorno entre una cosa y la otra).
    elegida = interfaz or Ajustes().portal.interfaz
    aplicacion = FastAPI(
        title="Centinela",
        version=VERSION,
        description=DESCRIPCION,
        summary="Monitoreo de fraude SINPE en tiempo real",
        lifespan=ciclo_vida,
        # H-25: el guardia aplica la tabla de permisos a TODA ruta. /docs y /redoc se
        # retiran (cargan JavaScript de una CDN); /openapi.json lo sirve `rutas_acceso`,
        # detras de sesion.
        dependencies=[Depends(guardia)],
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )

    aplicacion.state.interfaz = elegida
    registrar_manejadores(aplicacion)
    registrar_manejadores_sesion(aplicacion)
    if elegida == "spa":
        aplicacion.include_router(rutas_spa.enrutador)
    aplicacion.include_router(rutas_acceso.enrutador)
    aplicacion.include_router(rutas_salud.enrutador)
    aplicacion.include_router(rutas_estado.enrutador)
    aplicacion.include_router(enrutador_v1)
    aplicacion.include_router(rutas_paginas_tablero.enrutador)
    aplicacion.include_router(rutas_paginas_busqueda.enrutador)
    aplicacion.include_router(rutas_paginas_casos.enrutador)
    aplicacion.include_router(rutas_paginas.enrutador)

    # Los estaticos se sirven desde el propio proceso: es un monolito y no hay un
    # servidor web delante. `check_dir=False` no se usa a proposito -si la carpeta
    # desaparece, la aplicacion no arranca en vez de servir 404 en silencio.
    aplicacion.mount(
        "/estaticos", StaticFiles(directory=RUTA_ESTATICOS), name="estaticos"
    )

    return aplicacion


app = crear_aplicacion()
