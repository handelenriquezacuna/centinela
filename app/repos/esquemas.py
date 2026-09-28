"""Lectura y copia de los validadores `$jsonSchema` de las colecciones.

Sirve para una cosa concreta: que una prueba pueda verificar los modelos contra los
validadores REALES de la base del equipo, sin escribir en ella. Se leen los
validadores, se crean las mismas colecciones en una base desechable, y ahi se
comprueba que acepta y que rechaza.

Los validadores definitivos los escriben H-01 y H-02. Este modulo no los define: los
lee. Que el contrato del codigo y el de la base coincidan es lo que se verifica.
"""

from __future__ import annotations

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase


async def obtener_validadores(base: AsyncDatabase) -> dict[str, dict[str, Any]]:
    """Validador de cada coleccion que tenga uno, por nombre de coleccion."""
    validadores: dict[str, dict[str, Any]] = {}

    async for informacion in await base.list_collections():
        opciones = informacion.get("options") or {}
        validador = opciones.get("validator")
        if validador:
            validadores[informacion["name"]] = validador

    return validadores


async def recrear_con_validador(
    base: AsyncDatabase, nombre: str, validador: dict[str, Any]
) -> None:
    """Deja `nombre` vacia y con ese validador, en la base que se le pase.

    Solo para bases desechables: empieza tirando la coleccion.
    """
    await base.drop_collection(nombre)
    await base.create_collection(nombre, validator=validador)
