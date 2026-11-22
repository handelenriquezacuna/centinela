"""Consultas del disparador D1 (H-09): todo lo que D1 lee y escribe en Mongo.

D1 (`app/deteccion/d1.py`) no tiene una sola consulta: las pide aqui. Lo que vive en
este modulo, en el orden en que D1 lo usa por cada transaccion:

1. `observar_transacciones`: el change stream de `transacciones`, solo inserts.
2. `leer_reglas_activas` y `leer_politica_vigente`: se leen en CADA evento, no se
   cachean. Es lo que hace cierto el criterio de H-07 ("cambiar un umbral cambia el
   comportamiento sin tocar codigo"): la siguiente transaccion ya usa el umbral
   nuevo, sin reiniciar nada. A la escala del proyecto son dos lecturas por indice.
3. `leer_origen` y `armar_contexto`: los hechos que el evaluador puro (H-08) no
   puede buscar solo.
4. `insertar_alerta`: el indice unico de `alertas.transaccion_id` decide si es nueva.
5. `guardar_token`: DESPUES de la alerta, nunca antes.

`deteccion_estado` guarda dos clases de documento (validador en el bloque 13 de
`scripts/semilla/01-colecciones.js`): el token de reanudacion, uno por disparador, y
los eventos descartados tras agotar los reintentos.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.deteccion.evaluador import ContextoEvaluacion, PoliticaDeteccion, VentanaOrigen
from app.modelos import (
    COLECCION_ALERTAS,
    COLECCION_DETECCION_ESTADO,
    COLECCION_POLITICA,
    COLECCION_TRANSACCIONES,
    COLECCIONES,
    ESQUEMA_VERSION,
    Alerta,
    EntradaListaRiesgo,
    PerfilComportamiento,
    Regla,
    Transaccion,
)
from app.repos.carga_demo import a_entero_64
from app.repos.flujo_alertas import (
    CHANGE_STREAM_HISTORY_LOST,
    TokenDeReanudacionInvalido,
)

registro_log = logging.getLogger("centinela.deteccion")

# Solo inserts: una transaccion se escribe una vez y no se actualiza (es un agregado,
# ver `Transaccion`). Un update o un delete en `transacciones` no es una transferencia
# nueva y no se evalua.
#
# Pipeline y opciones se declaran una sola vez y se usan igual al abrir y al
# reanudar, por la misma razon que en `flujo_alertas.py`: reanudar con otro pipeline
# aplica un filtro nuevo a un punto del oplog elegido con el viejo. Un insert ya trae
# `fullDocument`; no hace falta `updateLookup`.
PIPELINE_TRANSACCIONES: list[dict[str, Any]] = [{"$match": {"operationType": "insert"}}]
OPCIONES_FLUJO_TRANSACCIONES: dict[str, Any] = {}



def _dominio(nombre: str) -> str:
    """Nombre de coleccion de dominio, verificado contra `COLECCIONES` al importar.

    Asi un nombre mal escrito revienta al cargar el modulo y no devuelve una coleccion
    vacia que Mongo crea en silencio.
    """
    if nombre not in COLECCIONES:
        raise KeyError(f"{nombre} no es una coleccion de dominio")
    return nombre


CUENTAS = _dominio("cuentas")
CLIENTES = _dominio("clientes")
PERFILES = _dominio("perfiles_comportamiento")
REGLAS = _dominio("reglas_deteccion")
LISTAS_RIESGO = _dominio("listas_riesgo")

# Los `tipo` de regla cuyo contexto depende de un parametro de la propia regla.
TIPO_DESTINO = "destino"
TIPO_VELOCIDAD = "velocidad"

# Cuando no hay regla activa de ese tipo, el contexto igual tiene que llevar un valor
# (todos sus campos son obligatorios). Con estos, la regla ausente no se evalua y
# ninguna otra los lee; si la regla existe pero no trae el parametro, el evaluador lo
# reporta como no evaluada antes de mirar el contexto.
DIAS_SIN_REGLA = 1
MINUTOS_SIN_REGLA = 1


class FlujoInvalidado(TokenDeReanudacionInvalido):
    """El change stream recibio `invalidate`: se borro o renombro `transacciones`.

    Es un token inservible mas: reanudar con `resumeAfter` desde antes del
    `invalidate` vuelve a entregarlo, y D1 giraria para siempre. Se trata igual que
    `ChangeStreamHistoryLost`: resincronizar y seguir desde ahora.
    """


@dataclass(frozen=True)
class EventoTransaccion:
    """Una vuelta del change stream de `transacciones`, sin la forma del driver.

    `documento` es la transaccion insertada, o `None` si la vuelta termino sin evento
    (el cursor espero `espera_cursor_ms` y no llego nada). La vuelta vacia tambien
    trae token: es el `postBatchResumeToken`, que apunta despues de todo lo ya visto.
    """

    token: Mapping[str, Any] | None
    documento: Mapping[str, Any] | None


@dataclass(frozen=True)
class DatosOrigen:
    """Lo que la alerta copia del cliente que envia, y lo que el contexto necesita."""

    cuenta_sinpe: str
    cliente_id: str
    cliente_nombre: str


@runtime_checkable
class ProtocoloRepoDeteccion(Protocol):
    """Lo que D1 necesita de este repositorio. El doble de prueba lo cumple igual."""

    def observar_transacciones(
        self, *, desde_token: Mapping[str, Any] | None, espera_ms: int
    ) -> AsyncIterator[EventoTransaccion]: ...

    async def leer_reglas_activas(self) -> list[Regla]: ...

    async def leer_politica_vigente(self) -> PoliticaDeteccion: ...

    async def leer_origen(self, transaccion: Transaccion) -> DatosOrigen: ...

    async def armar_contexto(
        self, transaccion: Transaccion, origen: DatosOrigen, reglas: Sequence[Regla]
    ) -> ContextoEvaluacion: ...

    async def insertar_alerta(self, alerta: Alerta) -> bool: ...

    async def leer_token(self, disparador: str) -> Mapping[str, Any] | None: ...

    async def guardar_token(
        self,
        disparador: str,
        token: Mapping[str, Any],
        *,
        transaccion_id: str | None,
    ) -> None: ...

    async def descartar(
        self, disparador: str, *, transaccion_id: str, motivo: str, intentos: int
    ) -> None: ...


def _parametro(reglas: Sequence[Regla], tipo: str, nombre: str) -> float | None:
    """Parametro de la primera regla activa de ese tipo, o None.

    Si hubiera dos reglas activas del mismo tipo con distinto parametro, el contexto se
    arma con la primera y la otra queda como no evaluada (el evaluador compara el
    parametro de la regla contra el del contexto). No se adivina.
    """
    for regla in reglas:
        if regla.activa and regla.tipo == tipo and nombre in regla.parametros:
            return regla.parametros[nombre]
    return None


class RepoDeteccion:
    """Acceso de D1 a la base."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base
        self._transacciones = base[COLECCION_TRANSACCIONES]
        self._alertas = base[COLECCION_ALERTAS]
        self._estado = base[COLECCION_DETECCION_ESTADO]

    # -- Change stream ----------------------------------------------------

    async def observar_transacciones(
        self, *, desde_token: Mapping[str, Any] | None, espera_ms: int
    ) -> AsyncIterator[EventoTransaccion]:
        """Itera las vueltas del change stream, reanudando desde `desde_token` si hay.

        `resumeAfter` y no `startAfter`: D1 quiere seguir DESPUES del ultimo evento
        cuyas escrituras ya termino. Se itera con `try_next` y no con `async for` para
        que una vuelta sin evento tambien vuelva a D1: asi D1 late sin trafico y
        `/estado` distingue "sin transacciones" de "atascado".

        Token inservible: un `OperationFailure` antes de completar la primera vuelta,
        abriendo CON token, se traduce en `TokenDeReanudacionInvalido`. El caso
        canonico es `ChangeStreamHistoryLost` (el oplog dio la vuelta); un token
        corrupto da otros codigos y significa lo mismo. Cualquier otro fallo sube tal
        cual, y D1 reabre desde el ultimo token guardado. Los errores reanudables (una
        eleccion de primario, un corte breve) los reintenta pymongo solo, con su token
        interno, y aqui no se ven.
        """
        vueltas = 0
        try:
            flujo = await self._transacciones.watch(
                PIPELINE_TRANSACCIONES,
                resume_after=desde_token,
                max_await_time_ms=espera_ms,
                **OPCIONES_FLUJO_TRANSACCIONES,
            )
            async with flujo:
                while flujo.alive:
                    cambio = await flujo.try_next()
                    vueltas += 1
                    if cambio is None:
                        yield EventoTransaccion(token=flujo.resume_token, documento=None)
                    elif cambio["operationType"] == "invalidate":
                        raise FlujoInvalidado(
                            "el change stream de transacciones se invalido "
                            "(la coleccion se borro o se renombro)"
                        )
                    else:
                        yield EventoTransaccion(
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
                    f"el token de reanudacion de transacciones ya no sirve ({etiqueta})"
                ) from fallo
            raise

    # -- Catalogo ---------------------------------------------------------

    async def leer_reglas_activas(self) -> list[Regla]:
        """Reglas con `activa: true`, ordenadas por codigo para que la alerta salga
        siempre con el mismo orden de reglas.

        Un documento que no valida contra `Regla` se salta y se registra: una regla mal
        cargada no puede detener la evaluacion de todas las transacciones.
        """
        reglas: list[Regla] = []
        cursor = self._base[REGLAS].find({"activa": True}).sort("codigo", 1)
        async for documento in cursor:
            try:
                reglas.append(Regla.model_validate(documento))
            except ValueError as fallo:
                registro_log.error(
                    "regla %s ignorada, no valida: %s", documento.get("_id"), fallo
                )
        return reglas

    async def leer_politica_vigente(self) -> PoliticaDeteccion:
        documento = await self._base[COLECCION_POLITICA].find_one({"vigente": True})
        if documento is None:
            raise LookupError("no hay politica_deteccion vigente; sin ella no hay cortes")
        return PoliticaDeteccion.model_validate(documento)

    # -- Contexto ---------------------------------------------------------

    async def leer_origen(self, transaccion: Transaccion) -> DatosOrigen:
        """Cuenta de origen -> numero SINPE y cliente. La alerta copia los dos."""
        cuenta = await self._base[CUENTAS].find_one({"_id": transaccion.cuenta_origen})
        if cuenta is None:
            raise LookupError(f"la cuenta de origen {transaccion.cuenta_origen} no existe")
        cliente = await self._base[CLIENTES].find_one(
            {"_id": cuenta["cliente_id"]}, {"nombre": 1}
        )
        if cliente is None:
            raise LookupError(f"el cliente {cuenta['cliente_id']} no existe")
        return DatosOrigen(
            cuenta_sinpe=cuenta["cuenta_sinpe"],
            cliente_id=cuenta["cliente_id"],
            cliente_nombre=cliente["nombre"],
        )

    async def armar_contexto(
        self, transaccion: Transaccion, origen: DatosOrigen, reglas: Sequence[Regla]
    ) -> ContextoEvaluacion:
        """Los hechos de Mongo que el evaluador necesita, con las ventanas de las reglas.

        Todas las ventanas miran hacia ATRAS desde `transaccion.fecha`, nunca desde
        ahora: si D1 reprocesa una transaccion de hace una hora, el resultado tiene que
        ser el mismo que cuando llego. Una transaccion posterior no cambia el pasado.
        """
        perfil_doc = await self._base[PERFILES].find_one(
            {"_id": origen.cliente_id}
        )
        perfil = (
            PerfilComportamiento.model_validate(perfil_doc) if perfil_doc is not None else None
        )

        dias = _parametro(reglas, TIPO_DESTINO, "dias_historial")
        dias_contexto = max(1, int(dias)) if dias is not None else DIAS_SIN_REGLA
        destinos = await self._destinos_conocidos(transaccion, origen, dias_contexto)

        entradas = [
            EntradaListaRiesgo.model_validate(documento)
            async for documento in self._base[LISTAS_RIESGO].find(
                {"cuenta": transaccion.cuenta_destino}
            )
        ]

        minutos = _parametro(reglas, TIPO_VELOCIDAD, "ventana_minutos")
        minutos_contexto = max(1, int(minutos)) if minutos is not None else MINUTOS_SIN_REGLA
        ventana = await self._ventana_origen(transaccion, minutos_contexto)

        return ContextoEvaluacion(
            perfil=perfil,
            dias_historial=dias_contexto,
            destinos_conocidos=destinos,
            entradas_lista_riesgo=tuple(entradas),
            ventana_origen=ventana,
        )

    async def _destinos_conocidos(
        self, transaccion: Transaccion, origen: DatosOrigen, dias: int
    ) -> dict[str, int]:
        """Transferencias previas DEL CLIENTE (todas sus cuentas) al mismo destino.

        Solo se cuenta el destino de esta transaccion: es la unica clave que el
        evaluador va a mirar. Estrictamente anteriores, y sin la propia transaccion.
        """
        cuentas = await self._base[CUENTAS].distinct(
            "_id", {"cliente_id": origen.cliente_id}
        )
        previas = await self._transacciones.count_documents(
            {
                "cuenta_origen": {"$in": cuentas},
                "cuenta_destino": transaccion.cuenta_destino,
                "fecha": {
                    "$gte": transaccion.fecha - timedelta(days=dias),
                    "$lt": transaccion.fecha,
                },
                "_id": {"$ne": transaccion.transaccion_id},
            }
        )
        return {transaccion.cuenta_destino: previas}

    async def _ventana_origen(self, transaccion: Transaccion, minutos: int) -> VentanaOrigen:
        """Rafaga de R-09: esta transaccion y las del mismo origen en los `minutos`
        anteriores. La propia se suma aparte para no depender de que ya este visible
        en la consulta, ni contarla dos veces."""
        resultado = await (
            await self._transacciones.aggregate(
                [
                    {
                        "$match": {
                            "cuenta_origen": transaccion.cuenta_origen,
                            "fecha": {
                                "$gte": transaccion.fecha - timedelta(minutes=minutos),
                                "$lte": transaccion.fecha,
                            },
                            "_id": {"$ne": transaccion.transaccion_id},
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "cantidad": {"$sum": 1},
                            "monto": {"$sum": "$monto_crc"},
                        }
                    },
                ]
            )
        ).to_list(length=1)
        cantidad = resultado[0]["cantidad"] if resultado else 0
        monto = int(resultado[0]["monto"]) if resultado else 0
        return VentanaOrigen(
            ventana_minutos=minutos,
            cantidad=cantidad + 1,
            monto_acumulado_crc=monto + transaccion.monto_crc,
        )

    # -- Escrituras -------------------------------------------------------

    async def insertar_alerta(self, alerta: Alerta) -> bool:
        """Inserta la alerta. `True` si es nueva, `False` si la transaccion ya tenia una.

        La no duplicacion la impone la base: el indice unico de
        `alertas.transaccion_id`. Una clave duplicada puede venir de ese indice o del
        `_id` (que se deriva de la transaccion), y el orden en que el servidor revisa
        los indices no esta garantizado. Por eso no se mira cual indice salto: se
        pregunta si ya existe una alerta PARA ESTA transaccion. Si existe, el evento ya
        estaba procesado. Si no existe, el `_id` esta tomado por otra transaccion; eso
        no es un reproceso, es un conflicto real, y sube.
        """
        try:
            await self._alertas.insert_one(a_entero_64(alerta.a_documento()))
            return True
        except DuplicateKeyError:
            existente = await self._alertas.find_one(
                {"transaccion_id": alerta.transaccion_id}, {"_id": 1}
            )
            if existente is not None:
                return False
            raise

    async def leer_token(self, disparador: str) -> Mapping[str, Any] | None:
        documento = await self._estado.find_one({"_id": disparador, "clase": "reanudacion"})
        return documento["token_reanudacion"] if documento is not None else None

    async def guardar_token(
        self,
        disparador: str,
        token: Mapping[str, Any],
        *,
        transaccion_id: str | None,
    ) -> None:
        """Reemplaza el token del disparador. Lo llama D1 cuando el evento termino."""
        documento: dict[str, Any] = {
            "_id": disparador,
            "clase": "reanudacion",
            "disparador": disparador,
            "token_reanudacion": dict(token),
            "actualizado_en": datetime.now(UTC),
            "esquema_version": ESQUEMA_VERSION,
        }
        if transaccion_id is not None:
            documento["ultima_transaccion_id"] = transaccion_id
        await self._estado.replace_one({"_id": disparador}, documento, upsert=True)

    async def descartar(
        self, disparador: str, *, transaccion_id: str, motivo: str, intentos: int
    ) -> None:
        """Deja el evento en el descarte. Reescribir el mismo es idempotente."""
        identificador = f"{disparador}:{transaccion_id}"
        await self._estado.replace_one(
            {"_id": identificador},
            {
                "_id": identificador,
                "clase": "descarte",
                "disparador": disparador,
                "transaccion_id": transaccion_id,
                "motivo": motivo[:1000] or "sin mensaje",
                "intentos": intentos,
                "actualizado_en": datetime.now(UTC),
                "esquema_version": ESQUEMA_VERSION,
            },
            upsert=True,
        )
