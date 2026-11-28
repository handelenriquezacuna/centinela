"""Apoyo compartido de las pruebas de D2 contra Mongo (T-77): ajustes, base y arranque de D2."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.deteccion.d2 import NOMBRE_D2, d2
from app.modelos import Severidad
from app.nucleo.config import AjustesNotificaciones
from app.repos.carga_demo import a_entero_64
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.notificaciones import RepoNotificaciones
from app.supervision import Supervisor
from pruebas.conftest import BASE_SEMILLA
from pruebas.dobles.alertas_d2 import alerta_documento

BASE_D2 = "antifraude_pruebas_d2"
CON_VALIDADOR = ("alertas", "notificaciones", "deteccion_estado")
LIMITE_SEGUNDOS = 20


def ajustes_de_prueba(puerto: int, **cambios: Any) -> AjustesNotificaciones:
    base: dict[str, Any] = {
        "smtp_host": "127.0.0.1",
        "smtp_puerto": puerto,
        "espera_smtp_segundos": 2,
        "espera_cursor_ms": 100,
        "reintentos_envio": 2,
        "espera_inicial_segundos": 0.05,
        "espera_maxima_segundos": 0.2,
        "refresco_reanudacion_segundos": 0.2,
        "portal_url": "http://127.0.0.1:8000",
    }
    return AjustesNotificaciones(**{**base, **cambios})


def puerto_cerrado() -> int:
    with socket.socket() as sonda:
        sonda.bind(("127.0.0.1", 0))
        return int(sonda.getsockname()[1])


async def base_d2_desechable(base_mongo: Any) -> AsyncIterator[Any]:
    """Base desechable con validadores e indices reales, sin datos (generador async)."""
    cliente = base_mongo.client
    validadores = await obtener_validadores(cliente[BASE_SEMILLA])
    faltan = [nombre for nombre in CON_VALIDADOR if nombre not in validadores]
    if faltan:
        pytest.skip(
            f"sin validador en {BASE_SEMILLA} para {faltan}; correr scripts/semilla/01-colecciones.js"
        )
    await cliente.drop_database(BASE_D2)
    base = cliente[BASE_D2]
    for nombre in CON_VALIDADOR:
        await recrear_con_validador(base, nombre, validadores[nombre])
    await base["alertas"].create_index("transaccion_id", unique=True, name="idx_alertas_transaccion_id")
    await base["notificaciones"].create_index(
        [("alerta_id", 1), ("canal", 1)], unique=True, name="idx_notificaciones_alerta_canal"
    )
    # T-82: el enlace del SMS se busca por su hash (unico y parcial) y la cola sube las
    # alertas "No fui yo" por su indice parcial; los mismos de 02-indices.js.
    await base["notificaciones"].create_index(
        "enlace_hash",
        unique=True,
        name="idx_notificaciones_enlace_hash",
        partialFilterExpression={"enlace_hash": {"$exists": True}},
    )
    await base["alertas"].create_index(
        [("puntaje", -1), ("fecha_creacion", -1), ("_id", 1)],
        name="idx_alertas_no_fui_yo",
        partialFilterExpression={"respuesta_cliente.valor": "no_fui_yo"},
    )
    await base["alertas"].create_index(
        [("puntaje", -1), ("fecha_creacion", -1), ("_id", 1)], name="idx_alertas_puntaje_fecha"
    )
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_D2)


async def esperar(condicion: Callable[[], Awaitable[bool]], que: str) -> None:
    limite = asyncio.get_running_loop().time() + LIMITE_SEGUNDOS
    while not await condicion():
        if asyncio.get_running_loop().time() > limite:
            raise AssertionError(f"pasaron {LIMITE_SEGUNDOS} s esperando {que}")
        await asyncio.sleep(0.05)


@asynccontextmanager
async def d2_corriendo(
    base: Any, ajustes: AjustesNotificaciones, *, aviso: Any = None
) -> AsyncIterator[Supervisor]:
    """D2 lanzado como en el lifespan. Cede el control cuando el flujo ya esta abierto
    (primer latido), para que ninguna insercion de la prueba ocurra antes del watch."""
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(
        NOMBRE_D2, d2(RepoNotificaciones(base), supervisor, ajustes=ajustes, aviso=aviso)
    )

    async def abierto() -> bool:
        return supervisor.informe().tarea(NOMBRE_D2).eventos_procesados >= 1

    try:
        await esperar(abierto, "el primer latido de D2")
        yield supervisor
    finally:
        await supervisor.detener()


async def insertar(base: Any, *numeros: int, severidad: Severidad = Severidad.CRITICA) -> None:
    puntaje = {Severidad.CRITICA: 75, Severidad.ALTA: 60, Severidad.MEDIA: 40}[severidad]
    await base["alertas"].insert_many(
        [a_entero_64(alerta_documento(n, severidad=severidad, puntaje=puntaje)) for n in numeros]
    )


async def token_en(base: Any, transaccion_id: str) -> None:
    async def llego() -> bool:
        estado = await base["deteccion_estado"].find_one({"_id": NOMBRE_D2})
        return estado is not None and estado.get("ultima_transaccion_id") == transaccion_id

    await esperar(llego, f"que D2 guarde el token de {transaccion_id}")


