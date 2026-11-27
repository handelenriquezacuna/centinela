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

from app.api.limitador import LimitadorIntentos
from app.nucleo.config import Ajustes, obtener_ajustes
from app.nucleo.db import obtener_base as _base_del_cliente
from app.repos.alertas import ProtocoloRepoAlertas, RepoAlertas
from app.repos.aviso_cliente import ProtocoloRepoRespuestaCliente, RepoRespuestaCliente
from app.repos.bitacora import ProtocoloRepoBitacora, RepoBitacora
from app.repos.busqueda import ProtocoloRepoBusqueda, RepoBusqueda
from app.repos.casos_destino import ProtocoloRepoCasosDestino, RepoCasosDestino
from app.repos.detalle import ProtocoloRepoDetalle, RepoDetalle
from app.repos.flujo_alertas import ProtocoloFlujoAlertas, RepoFlujoAlertas
from app.repos.notificaciones import (
    ProtocoloRepoLecturaNotificaciones,
    RepoLecturaNotificaciones,
)
from app.repos.red_mulas import RepoRedMulas
from app.repos.reglas import ProtocoloRepoReglas, RepoReglas
from app.repos.tablero import ProtocoloRepoTablero, RepoTablero
from app.respaldos.servicio import ServicioRespaldos
from app.supervision import Supervisor


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


def dep_repo_casos_destino(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloRepoCasosDestino:
    return RepoCasosDestino(base)


def dep_repo_busqueda(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloRepoBusqueda:
    return RepoBusqueda(base)


def dep_repo_reglas(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloRepoReglas:
    return RepoReglas(base)


def dep_repo_detalle(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloRepoDetalle:
    return RepoDetalle(base)


def dep_repo_tablero(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloRepoTablero:
    return RepoTablero(base)


def dep_repo_red_mulas_portal(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> RepoRedMulas:
    return RepoRedMulas(base)


def dep_repo_notificaciones(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloRepoLecturaNotificaciones:
    return RepoLecturaNotificaciones(base)


def dep_repo_respuesta_cliente(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloRepoRespuestaCliente:
    """El repositorio del portal del cliente (T-82): el enlace del SMS y su respuesta."""
    return RepoRespuestaCliente(base)


def dep_limitador(peticion: Request) -> LimitadorIntentos:
    """El limite de abuso del endpoint publico del cliente: uno solo por proceso, en memoria."""
    limitador: LimitadorIntentos | None = getattr(peticion.app.state, "limitador_cliente", None)
    if limitador is None:
        ajustes = dep_ajustes(peticion).aviso_cliente
        limitador = LimitadorIntentos(
            invalidos_por_ip=ajustes.intentos_invalidos_por_ip,
            respuestas_por_enlace=ajustes.respuestas_por_enlace,
            ventana_segundos=ajustes.ventana_segundos,
        )
        peticion.app.state.limitador_cliente = limitador
    return limitador


def dep_flujo_alertas(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloFlujoAlertas:
    """El observador del change stream de `alertas` que alimenta el canal SSE."""
    return RepoFlujoAlertas(base)


def dep_servicio_respaldos(peticion: Request) -> ServicioRespaldos:
    """El servicio de respaldos del proceso: uno solo, porque su cerrojo ES la regla de
    "una operacion a la vez". Lo crea el lifespan."""
    servicio: ServicioRespaldos | None = getattr(peticion.app.state, "servicio_respaldos", None)
    if servicio is None:  # pragma: no cover - contrato del lifespan
        raise RuntimeError("la app se armo sin lifespan: no hay servicio de respaldos")
    return servicio


def dep_supervisor(peticion: Request) -> Supervisor:
    """El supervisor de tareas de fondo, creado en el lifespan.

    Si la aplicacion se armo sin lifespan no hay supervisor, y en vez de inventar uno
    vacio se devuelve uno sin tareas: `/estado` responde `sin_supervision`, que es la
    verdad. Inventar un supervisor con tareas imaginarias haria que la ruta mintiera
    justo en el escenario que viene a detectar.
    """
    supervisor: Supervisor | None = getattr(peticion.app.state, "supervisor", None)
    if supervisor is None:  # pragma: no cover - contrato del lifespan
        ajustes = dep_ajustes(peticion)
        return Supervisor(
            tolerancia_segundos=ajustes.supervision.tolerancia_retraso_segundos
        )
    return supervisor


AjustesDep = Annotated[Ajustes, Depends(dep_ajustes)]
def dep_repo_bitacora(
    base: Annotated[AsyncDatabase, Depends(dep_base)],
) -> ProtocoloRepoBitacora:
    return RepoBitacora(base)


BaseDep = Annotated[AsyncDatabase, Depends(dep_base)]
RepoAlertasDep = Annotated[ProtocoloRepoAlertas, Depends(dep_repo_alertas)]
RepoCasosDestinoDep = Annotated[ProtocoloRepoCasosDestino, Depends(dep_repo_casos_destino)]
RepoBusquedaDep = Annotated[ProtocoloRepoBusqueda, Depends(dep_repo_busqueda)]
RepoReglasDep = Annotated[ProtocoloRepoReglas, Depends(dep_repo_reglas)]
RepoDetalleDep = Annotated[ProtocoloRepoDetalle, Depends(dep_repo_detalle)]
FlujoAlertasDep = Annotated[ProtocoloFlujoAlertas, Depends(dep_flujo_alertas)]
SupervisorDep = Annotated[Supervisor, Depends(dep_supervisor)]
RepoTableroDep = Annotated[ProtocoloRepoTablero, Depends(dep_repo_tablero)]
RepoNotificacionesDep = Annotated[
    ProtocoloRepoLecturaNotificaciones, Depends(dep_repo_notificaciones)
]
RepoBitacoraDep = Annotated[ProtocoloRepoBitacora, Depends(dep_repo_bitacora)]
RepoRespuestaClienteDep = Annotated[
    ProtocoloRepoRespuestaCliente, Depends(dep_repo_respuesta_cliente)
]
LimitadorDep = Annotated[LimitadorIntentos, Depends(dep_limitador)]
RepoRedMulasPortalDep = Annotated[RepoRedMulas, Depends(dep_repo_red_mulas_portal)]
ServicioRespaldosDep = Annotated[ServicioRespaldos, Depends(dep_servicio_respaldos)]
