"""H-11 · Acceso de D2 y del portal a `notificaciones`.

Dos clases, las dos aqui porque ninguna consulta a Mongo vive fuera de `app/repos/`:

- `RepoNotificaciones`: lo que necesita el disparador D2 (el change stream de `alertas`
  filtrado a CRITICA, el registro de cada envio, el token de reanudacion y el descarte).
  El token y el descarte se delegan en `RepoDeteccion`: es el mismo documento de
  `deteccion_estado` y el mismo contrato que usa D1, con otro `_id`.
- `RepoLecturaNotificaciones`: la lista paginada que lee el supervisor en el portal.

Idempotencia: una alerta produce UNA notificacion por canal. La impone la base con el
indice unico `(alerta_id, canal)` de `02-indices.js` y, ademas, el `_id` es derivado de
la alerta (`ALR-000123` -> `NOT-000123`), asi que reprocesar el mismo evento choca de
cualquier forma. Es la misma idea que `alertas.transaccion_id` en D1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.modelos import COLECCION_ALERTAS
from app.repos.deteccion import FlujoInvalidado, RepoDeteccion
from app.repos.flujo_alertas import (
    CHANGE_STREAM_HISTORY_LOST,
    TokenDeReanudacionInvalido,
)

COLECCION_NOTIFICACIONES = "notificaciones"

# Solo inserts de alertas CRITICAS, filtrados en el servidor, mas `invalidate` para que
# D2 se entere de que borraron la coleccion (re-sembrar con 01-colecciones.js) en vez de
# reabrir un cursor muerto en bucle.
#
# Por que solo inserts y no tambien los updates que "suben a CRITICA": la severidad de
# una alerta se fija al crearla (D1) y ningun camino del sistema la reescribe despues; las
# transiciones solo mueven `estado`. Escuchar updates agregaria `updateLookup` y un filtro
# sobre el estado nuevo sin que exista el caso que lo necesite.
#
# Pipeline y opciones se declaran una vez y las dos aperturas (nueva y reanudada) las
# comparten: reanudar con otro filtro aplica un filtro nuevo a un punto del oplog elegido
# con el viejo.
PIPELINE_ALERTAS_CRITICAS: list[dict[str, Any]] = [
    {
        "$match": {
            "$or": [
                {"operationType": "insert", "fullDocument.severidad": "CRITICA"},
                {"operationType": "invalidate"},
            ]
        }
    }
]
OPCIONES_FLUJO_ALERTAS: dict[str, Any] = {}

ESTADOS_TERMINALES = ("enviado", "fallido")


@dataclass(frozen=True)
class EventoAlerta:
    """Una vuelta del change stream de alertas CRITICAS, sin la forma del driver.

    `documento` es la alerta insertada, o `None` si la vuelta termino sin evento (el
    cursor espero y no llego nada). La vuelta vacia tambien trae token: el
    `postBatchResumeToken`.
    """

    token: Mapping[str, Any] | None
    documento: Mapping[str, Any] | None


@runtime_checkable
class ProtocoloRepoNotificaciones(Protocol):
    """Lo que D2 necesita de este repositorio. El doble de prueba lo cumple igual."""

    def observar_alertas_criticas(
        self, *, desde_token: Mapping[str, Any] | None, espera_ms: int
    ) -> AsyncIterator[EventoAlerta]: ...

    async def crear_pendiente(self, documento: Mapping[str, Any]) -> bool: ...

    async def leer_notificacion(self, notificacion_id: str) -> Mapping[str, Any] | None: ...

    async def registrar_intento(
        self,
        notificacion_id: str,
        *,
        estado_envio: str,
        intentos: int,
        fecha: datetime,
        detalle_error: str | None,
    ) -> None: ...

    async def leer_token(self, disparador: str) -> Mapping[str, Any] | None: ...

    async def guardar_token(
        self, disparador: str, token: Mapping[str, Any], *, transaccion_id: str | None
    ) -> None: ...

    async def descartar(
        self, disparador: str, *, transaccion_id: str, motivo: str, intentos: int
    ) -> None: ...


class RepoNotificaciones:
    """Acceso de D2 a la base."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._alertas = base[COLECCION_ALERTAS]
        self._notificaciones = base[COLECCION_NOTIFICACIONES]
        # Token y descarte: el mismo codigo y documento que D1, con el `_id` de D2.
        self._estado = RepoDeteccion(base)

    # -- Change stream ----------------------------------------------------

    async def observar_alertas_criticas(
        self, *, desde_token: Mapping[str, Any] | None, espera_ms: int
    ) -> AsyncIterator[EventoAlerta]:
        """Itera las vueltas del change stream, reanudando desde `desde_token` si hay.

        Igual que `RepoDeteccion.observar_transacciones`: `resumeAfter` y `try_next`, de
        modo que una vuelta sin evento tambien vuelve a D2 (late sin trafico). Un token
        inservible (`ChangeStreamHistoryLost` u otro fallo antes de la primera vuelta,
        abriendo con token) y un `invalidate` se traducen en
        `TokenDeReanudacionInvalido`; cualquier otro fallo sube tal cual.
        """
        vueltas = 0
        try:
            flujo = await self._alertas.watch(
                PIPELINE_ALERTAS_CRITICAS,
                resume_after=desde_token,
                max_await_time_ms=espera_ms,
                **OPCIONES_FLUJO_ALERTAS,
            )
            async with flujo:
                while flujo.alive:
                    cambio = await flujo.try_next()
                    vueltas += 1
                    if cambio is None:
                        yield EventoAlerta(token=flujo.resume_token, documento=None)
                    elif cambio["operationType"] == "invalidate":
                        raise FlujoInvalidado(
                            "el change stream de alertas se invalido "
                            "(la coleccion se borro o se renombro)"
                        )
                    else:
                        yield EventoAlerta(
                            token=cambio["_id"], documento=cambio.get("fullDocument")
                        )
        except OperationFailure as fallo:
            if desde_token is not None and vueltas == 0:
                etiqueta = (
                    "ChangeStreamHistoryLost"
                    if fallo.code == CHANGE_STREAM_HISTORY_LOST
                    else f"codigo {fallo.code}"
                )
                raise TokenDeReanudacionInvalido(
                    f"el token de reanudacion de alertas ya no sirve ({etiqueta})"
                ) from fallo
            raise

    # -- Notificaciones ---------------------------------------------------

    async def crear_pendiente(self, documento: Mapping[str, Any]) -> bool:
        """Inserta la notificacion `pendiente`. `False` si ya existia (reproceso).

        La no duplicacion la impone la base: el `_id` derivado de la alerta y el indice
        unico `(alerta_id, canal)`. Este metodo solo traduce la clave duplicada.
        """
        try:
            await self._notificaciones.insert_one(dict(documento))
        except DuplicateKeyError:
            return False
        return True

    async def leer_notificacion(self, notificacion_id: str) -> Mapping[str, Any] | None:
        return await self._notificaciones.find_one({"_id": notificacion_id})

    async def registrar_intento(
        self,
        notificacion_id: str,
        *,
        estado_envio: str,
        intentos: int,
        fecha: datetime,
        detalle_error: str | None,
    ) -> None:
        """Deja el resultado de un intento de envio en la notificacion.

        `enviado` borra el motivo de un intento anterior fallido: el documento cuenta el
        estado final, y el numero de `intentos` dice cuanto costo.
        """
        cambios: dict[str, Any] = {
            "$set": {"estado_envio": estado_envio, "intentos": intentos, "fecha": fecha}
        }
        if detalle_error is not None:
            cambios["$set"]["detalle_error"] = detalle_error[:1000] or "sin mensaje"
        else:
            cambios["$unset"] = {"detalle_error": ""}
        await self._notificaciones.update_one({"_id": notificacion_id}, cambios)

    # -- Token y descarte (los mismos que D1, otro disparador) ------------

    async def leer_token(self, disparador: str) -> Mapping[str, Any] | None:
        return await self._estado.leer_token(disparador)

    async def guardar_token(
        self, disparador: str, token: Mapping[str, Any], *, transaccion_id: str | None
    ) -> None:
        await self._estado.guardar_token(disparador, token, transaccion_id=transaccion_id)

    async def descartar(
        self, disparador: str, *, transaccion_id: str, motivo: str, intentos: int
    ) -> None:
        await self._estado.descartar(
            disparador, transaccion_id=transaccion_id, motivo=motivo, intentos=intentos
        )


@runtime_checkable
class ProtocoloRepoLecturaNotificaciones(Protocol):
    async def listar(
        self, *, estado_envio: str | None, limite: int, desplazamiento: int
    ) -> tuple[list[dict[str, Any]], int]: ...


class RepoLecturaNotificaciones:
    """Lectura de las notificaciones para el supervisor (GET /api/v1/notificaciones)."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._notificaciones = base[COLECCION_NOTIFICACIONES]

    async def listar(
        self, *, estado_envio: str | None, limite: int, desplazamiento: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Las mas recientes primero, con los datos de la alerta que la origino.

        El `$lookup` es sobre la pagina ya acotada (`$limit` antes), por `_id` de alertas:
        una busqueda por clave por fila, no un recorrido. La alerta puede no existir mas
        (re-sembrada): el elemento sale igual, sin esos datos.
        """
        filtro: dict[str, Any] = {}
        if estado_envio is not None:
            filtro["estado_envio"] = estado_envio
        total = await self._notificaciones.count_documents(filtro)
        etapas: list[dict[str, Any]] = [
            {"$match": filtro},
            {"$sort": {"fecha": -1, "_id": -1}},
            {"$skip": desplazamiento},
            {"$limit": limite},
            {
                "$lookup": {
                    "from": COLECCION_ALERTAS,
                    "localField": "alerta_id",
                    "foreignField": "_id",
                    "pipeline": [
                        {
                            "$project": {
                                "severidad": 1,
                                "puntaje": 1,
                                "monto_crc": 1,
                                "cliente_nombre": 1,
                            }
                        }
                    ],
                    "as": "alerta",
                }
            },
        ]
        cursor = await self._notificaciones.aggregate(etapas)
        documentos = await cursor.to_list(length=None)
        return documentos, total
