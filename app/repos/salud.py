"""Comprobacion de que la base responde.

El ping vive aqui y no en `app/nucleo/db.py` por una razon concreta: un ping es una
consulta, y ninguna consulta va fuera de `app/repos/`. Si estuviera en `db.py`, la
prueba de arquitectura fallaria el primer dia, y la regla que se incumple el primer
dia no es una regla.
"""

from __future__ import annotations

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase


async def estado_conexion(base: AsyncDatabase) -> dict[str, Any]:
    """Pregunta a la base si esta viva y en que estado esta el replica set.

    Se usa `hello`, que es el comando barato y sin privilegios que ya responde el
    nombre del conjunto y quien es el primario. No se usa `rs.status()`, que pide
    permisos de administracion y devuelve mucho mas de lo que hace falta.

    No lanza: devuelve el diagnostico. Quien llama decide el codigo HTTP, porque
    "la base no responde" es una respuesta valida de una ruta de salud, no un error
    del servidor.
    """
    try:
        respuesta = await base.client.admin.command("hello")
    except Exception as fallo:  # noqa: BLE001 - el diagnostico se reporta, no se traga
        return {
            "conectado": False,
            "replica_set": None,
            "es_primario": None,
            "detalle": f"{type(fallo).__name__}: {fallo}",
        }

    return {
        "conectado": True,
        "replica_set": respuesta.get("setName"),
        "es_primario": bool(respuesta.get("isWritablePrimary")),
        "detalle": None,
    }
