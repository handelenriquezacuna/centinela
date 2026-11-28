"""T-82 · Acceso a la base del aviso al cliente: lo que D2 escribe y lo que el portal del cliente lee.

Dos clases, las dos aqui porque ninguna consulta a Mongo vive fuera de `app/repos/`:

- `RepoAviso`: lo que necesita el paso de D2 que manda el SMS (el contacto del cliente, la
  notificacion `sms` y su enlace).
- `RepoRespuestaCliente`: lo que necesita el portal del cliente (buscar el enlace por el hash
  de su token, leer la alerta, registrar la respuesta y alimentar la bandeja del simulador).

El token en claro NO pasa por este modulo hacia la base: llega ya convertido en hash.

Registrar la respuesta toca dos documentos (la notificacion y la alerta) y se hace en UNA
transaccion, como escalar: o quedan los dos o ninguno. Asi un enlace nunca queda gastado sin
que la alerta se entere, ni la alerta anotada con un enlace que sigue vivo.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pymongo import DESCENDING, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.modelos import COLECCION_ALERTAS, COLECCION_TRANSACCIONES

COLECCION_NOTIFICACIONES = "notificaciones"
COLECCION_CUENTAS = "cuentas"
COLECCION_CLIENTES = "clientes"
CANAL_SMS = "sms"

# Lo unico de la alerta que el portal del cliente necesita; el resto no sale de la base.
CAMPOS_ALERTA_CLIENTE = {
    "estado": 1,
    "monto_crc": 1,
    "fecha_creacion": 1,
    "cuenta_origen": 1,
    "cuenta_destino": 1,
    "respuesta_cliente": 1,
}


class EnlaceNoDisponible(Exception):
    """El enlace no existe, venció o ya se uso: no se puede registrar la respuesta."""


class AlertaDelEnlaceNoExiste(Exception):
    """La notificacion apunta a una alerta que ya no esta (base re-sembrada)."""


@runtime_checkable
class ProtocoloRepoRespuestaCliente(Protocol):
    """Lo que el portal del cliente necesita. Las pruebas de contrato inyectan un doble."""

    async def notificacion_por_hash(self, enlace_hash: str) -> dict[str, Any] | None: ...

    async def alerta_del_enlace(self, alerta_id: str) -> dict[str, Any] | None: ...

    async def registrar_respuesta(
        self, enlace_hash: str, valor: str, ahora: datetime
    ) -> dict[str, Any]: ...

    async def bandeja_sms(self, limite: int) -> list[dict[str, Any]]: ...


class RepoAviso:
    """Acceso del paso de D2 que avisa al cliente."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._transacciones = base[COLECCION_TRANSACCIONES]
        self._cuentas = base[COLECCION_CUENTAS]
        self._clientes = base[COLECCION_CLIENTES]
        self._notificaciones = base[COLECCION_NOTIFICACIONES]

    async def contacto_de_alerta(self, transaccion_id: str) -> Mapping[str, Any] | None:
        """Telefono y consentimiento del titular de la cuenta de origen de la transaccion.

        Tres lecturas por `_id`: transaccion (`CTA-xxx`), cuenta (`cliente_id`), cliente. La
        alerta no guarda `cliente_id`; resolverlo aqui evita agregarlo a toda alerta.
        """
        transaccion = await self._transacciones.find_one(
            {"_id": transaccion_id}, {"cuenta_origen": 1}
        )
        if transaccion is None:
            return None
        cuenta = await self._cuentas.find_one({"_id": transaccion["cuenta_origen"]}, {"cliente_id": 1})
        if cuenta is None:
            return None
        return await self._clientes.find_one(
            {"_id": cuenta["cliente_id"]}, {"telefono": 1, "acepta_sms": 1}
        )

    async def crear_pendiente(self, documento: Mapping[str, Any]) -> bool:
        """Inserta la notificacion `sms`. `False` si ya existia (reproceso): la base lo impone."""
        try:
            await self._notificaciones.insert_one(dict(documento))
        except DuplicateKeyError:
            return False
        return True

    async def leer_notificacion(self, notificacion_id: str) -> Mapping[str, Any] | None:
        return await self._notificaciones.find_one({"_id": notificacion_id})

    async def rotar_enlace(
        self, notificacion_id: str, *, enlace_hash: str, enlace_vence: datetime
    ) -> None:
        await self._notificaciones.update_one(
            {"_id": notificacion_id},
            {"$set": {"enlace_hash": enlace_hash, "enlace_vence": enlace_vence}},
        )

    async def registrar_intento(
        self,
        notificacion_id: str,
        *,
        estado_envio: str,
        intentos: int,
        fecha: datetime,
        detalle_error: str | None,
        texto: str | None,
    ) -> None:
        cambios: dict[str, Any] = {
            "$set": {"estado_envio": estado_envio, "intentos": intentos, "fecha": fecha}
        }
        if texto is not None:
            cambios["$set"]["texto"] = texto
        if detalle_error is not None:
            cambios["$set"]["detalle_error"] = detalle_error[:1000] or "sin mensaje"
        else:
            cambios["$unset"] = {"detalle_error": ""}
        await self._notificaciones.update_one({"_id": notificacion_id}, cambios)


class RepoRespuestaCliente:
    """Acceso del portal del cliente (publico) y de la bandeja del simulador."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base
        self._notificaciones = base[COLECCION_NOTIFICACIONES]
        self._alertas = base[COLECCION_ALERTAS]

    async def notificacion_por_hash(self, enlace_hash: str) -> dict[str, Any] | None:
        return await self._notificaciones.find_one(
            {"enlace_hash": enlace_hash, "canal": CANAL_SMS}
        )

    async def alerta_del_enlace(self, alerta_id: str) -> dict[str, Any] | None:
        return await self._alertas.find_one({"_id": alerta_id}, CAMPOS_ALERTA_CLIENTE)

    async def registrar_respuesta(
        self, enlace_hash: str, valor: str, ahora: datetime
    ) -> dict[str, Any]:
        """Gasta el enlace y anota la respuesta en la alerta, en una transaccion.

        El filtro de la notificacion es la regla de un solo uso: el hash coincide, NO esta
        usado y NO ha vencido. Dos respuestas a la vez: solo una encuentra el documento.
        La alerta recibe la marca y una entrada de historial SIN agente; su `estado` no se toca.
        """
        respuesta = {"valor": valor, "fecha": ahora}

        async def operar(sesion: AsyncClientSession) -> dict[str, Any]:
            nota = await self._notificaciones.find_one_and_update(
                {
                    "enlace_hash": enlace_hash,
                    "canal": CANAL_SMS,
                    "enlace_usado_en": {"$exists": False},
                    "enlace_vence": {"$gt": ahora},
                },
                {"$set": {"enlace_usado_en": ahora, "respuesta_cliente": respuesta}},
                return_document=ReturnDocument.AFTER,
                session=sesion,
            )
            if nota is None:
                raise EnlaceNoDisponible
            texto = "no fui yo" if valor == "no_fui_yo" else "si fui yo"
            actualizada = await self._alertas.update_one(
                {"_id": nota["alerta_id"]},
                {
                    "$set": {"respuesta_cliente": respuesta},
                    "$push": {
                        "historial": {
                            "accion": "respuesta_cliente",
                            "agente_id": None,
                            "fecha": ahora,
                            "nota": f"El cliente respondió desde su enlace: {texto}",
                        }
                    },
                },
                session=sesion,
            )
            if actualizada.matched_count == 0:
                raise AlertaDelEnlaceNoExiste
            return nota

        async with self._base.client.start_session() as sesion:
            return await sesion.with_transaction(operar)

    async def bandeja_sms(self, limite: int) -> list[dict[str, Any]]:
        """Los SMS mas recientes con su texto: lo que muestra el simulador de telefono.

        Solo `canal: sms`, de `fecha` descendente (`idx_notificaciones_fecha`). El hash no sale.
        """
        cursor = (
            self._notificaciones.find(
                {"canal": CANAL_SMS},
                {"enlace_hash": 0},
            )
            .sort([("fecha", DESCENDING), ("_id", DESCENDING)])
            .limit(limite)
        )
        return await cursor.to_list(length=limite)
