"""Dependencias de FastAPI: de donde salen los ajustes, la base y los repositorios.

Un solo cliente de Mongo por proceso, creado en el lifespan y guardado en
`aplicacion.state`. Las rutas no lo construyen ni lo importan: lo reciben.

Que esto sean dependencias y no importaciones directas es lo que permite que las
pruebas de contrato de la API sustituyan el repositorio con `dependency_overrides` y
corran en verde sin Mongo arriba.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.nucleo.config import Ajustes, obtener_ajustes
from app.nucleo.db import obtener_base as _base_del_cliente
from app.repos.alertas import ProtocoloRepoAlertas, RepoAlertas


def dep_ajustes(peticion: Request) -> Ajustes:
    """Ajustes del proceso.

    Se leen del estado de la aplicacion, donde los dejo el lifespan, y no se vuelven
    a construir: asi una peticion nunca lee el disco ni el entorno.
    """
    ajustes: Ajustes | None = getattr(peticion.app.state, "ajustes", None)
    if ajustes is None:  # pragma: no cover - solo si se arma la app sin lifespan
        return obtener_ajustes()
    return ajustes


def dep_cliente(peticion: Request) -> AsyncMongoClient:
    cliente: AsyncMongoClient | None = getattr(
        peticion.app.state, "cliente_mongo", None
    )
    if cliente is None:  # pragma: no cover - contrato del lifespan
        raise RuntimeError(
            "no hay cliente de Mongo en el estado de la aplicacion: la app se armo "
            "sin disparar el lifespan. En pruebas hay que usar LifespanManager."
        )
    return cliente


def dep_base(
    cliente: Annotated[AsyncMongoClient, Depends(dep_cliente)],
    ajustes: Annotated[Ajustes, Depends(dep_ajustes)],
) -> AsyncDatabase:
    return _base_del_cliente(cliente, ajustes)


def dep_repo_alertas(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloRepoAlertas:
    return RepoAlertas(base)


AjustesDep = Annotated[Ajustes, Depends(dep_ajustes)]
BaseDep = Annotated[AsyncDatabase, Depends(dep_base)]
RepoAlertasDep = Annotated[ProtocoloRepoAlertas, Depends(dep_repo_alertas)]
