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

Desde T-62 D1 procesa el change stream por lotes (`deteccion.lote_maximo`) y el
repositorio ofrece la version por lote de cada lectura: `leer_origenes`,
`armar_contextos` e `insertar_alertas`. Leen para todo el lote con `$in` y cuentan en
memoria con la fecha de cada transaccion (ver `app/deteccion/contexto_lote.py`); las
versiones por transaccion siguen, porque son el camino de un evento solo y el de
aislamiento de un evento fallido.

`deteccion_estado` guarda cuatro clases de documento (validador en el bloque 13 de
`scripts/semilla/01-colecciones.js`): el token de reanudacion, uno por disparador, los
eventos descartados tras agotar los reintentos y, desde T-63, los rasgos de grafo por
cuenta y su documento de corrida (los escribe D3, `app/repos/rasgos_grafo.py`; D1 solo los
lee, para R-11).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import BulkWriteError, DuplicateKeyError, OperationFailure

from app.deteccion.contexto_lote import (
    HistorialPorOrigen,
    a_movimiento,
    movimiento_de_documento,
    previas_al_destino,
    ventana_de_origen,
)
from app.deteccion.evaluador import (
    ContextoEvaluacion,
    PoliticaDeteccion,
    RasgoRed,
    VentanaOrigen,
)
from app.deteccion.rasgos_grafo import TIPO_REGLA_RED
from app.modelos import (
    COLECCION_ALERTAS,
    COLECCION_DETECCION_ESTADO,
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
from app.repos.ids_transaccion import (
    ID_MAXIMO,
    ID_MINIMO,
    PATRON_ID_DE_SEIS,
    es_id_de_seis_digitos,
)
from app.repos.rasgos_grafo import RepoRasgosGrafo
from app.repos.reglas import leer_politica_vigente

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
TIPO_RED_MULAS = TIPO_REGLA_RED

# Cuando no hay regla activa de ese tipo, el contexto igual tiene que llevar un valor
# (todos sus campos son obligatorios). Con estos, la regla ausente no se evalua y
# ninguna otra los lee; si la regla existe pero no trae el parametro, el evaluador lo
# reporta como no evaluada antes de mirar el contexto.
DIAS_SIN_REGLA = 1
MINUTOS_SIN_REGLA = 1

# Codigo de MongoDB de "clave duplicada" y proyeccion minima del historial por lote.
_CLAVE_DUPLICADA = 11000
_PROYECCION_MOVIMIENTO = {"cuenta_origen": 1, "cuenta_destino": 1, "fecha": 1, "monto_crc": 1}


def _hora_de_insercion(cambio: Mapping[str, Any]) -> datetime | None:
    """Hora de servidor de un evento del change stream, o `None` si no la trae."""
    hora = cambio.get("wallTime")
    if isinstance(hora, datetime):
        return hora if hora.tzinfo is not None else hora.replace(tzinfo=UTC)
    marca = cambio.get("clusterTime")
    if marca is not None and hasattr(marca, "as_datetime"):
        return marca.as_datetime()
    return None


def filtro_pendientes(ultima_transaccion_id: str) -> dict[str, Any]:
    """Transacciones posteriores a la ultima que D1 termino: ids de seis digitos mayores.

    Contrato de ids (`app/repos/ids_transaccion.py`): lo que entra despues de la siembra
    se numera `TXN-NNNNNN`, el siguiente al mayor de seis digitos, en orden de insercion.
    El orden por `_id` es entonces el orden en que D1 las recibe, y el de texto coincide
    con el numerico porque el ancho es fijo.

    Solo cuentan ids de seis digitos. Si `ultima_transaccion_id` no lo es (D1 proceso una
    de la demo sembrada, `TXN-007`), el limite inferior es `TXN-000000`; sin ese recorte,
    `"TXN-000500" > "TXN-007"` es falso y una carga masiva se veria con rezago 0.

    El rango de texto no basta: `"TXN-001" > "TXN-000500"` es verdadero (el id de tres
    digitos queda entre los de seis en el indice), y la demo sembrada se contaria como
    pendiente para siempre. El `$regex` anclado deja fuera todo id que no sea de seis
    digitos; MongoDB lo evalua sobre la clave del indice, antes de leer el documento: el
    conteo sigue siendo un recorrido del indice de `_id` sin leer un solo documento
    (`totalDocsExamined` 0; el plan pasa de COUNT_SCAN a IXSCAN), y la mas vieja es leer
    una (`nReturned` 1, `totalDocsExamined` 1).
    """
    desde = ultima_transaccion_id if es_id_de_seis_digitos(ultima_transaccion_id) else ID_MINIMO
    return {"_id": {"$gt": desde, "$lte": ID_MAXIMO, "$regex": PATRON_ID_DE_SEIS}}


@dataclass(frozen=True)
class RezagoMedido:
    """Transacciones sin evaluar y `fecha` (UTC) de la mas vieja; `None` sin pendientes."""

    pendientes: int
    mas_vieja: datetime | None


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
    # Cuando MongoDB inserto la transaccion (hora del servidor, UTC), segun el evento:
    # `wallTime` (milisegundos) o, si falta, `clusterTime` (segundos). NO es
    # `Transaccion.fecha`, que es la hora del negocio y puede ser historica. De aqui
    # sale la latencia de deteccion (H-09).
    insertada_en: datetime | None = None


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

    async def leer_origenes(
        self, transacciones: Sequence[Transaccion]
    ) -> dict[str, DatosOrigen | Exception]: ...

    async def armar_contextos(
        self,
        transacciones: Sequence[Transaccion],
        origenes: Mapping[str, DatosOrigen],
        reglas: Sequence[Regla],
    ) -> dict[str, ContextoEvaluacion | Exception]: ...

    async def insertar_alertas(self, alertas: Sequence[Alerta]) -> dict[str, bool | Exception]: ...

    async def leer_token(self, disparador: str) -> Mapping[str, Any] | None: ...

    async def medir_rezago(self, disparador: str) -> RezagoMedido: ...

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
        self._rasgos = RepoRasgosGrafo(base)

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
                            token=cambio["_id"],
                            documento=cambio.get("fullDocument"),
                            insertada_en=_hora_de_insercion(cambio),
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
        """La misma lectura que la API de reglas (T-19). Sin politica no hay cortes."""
        politica = await leer_politica_vigente(self._base)
        if politica is None:
            raise LookupError("no hay politica_deteccion vigente; sin ella no hay cortes")
        return politica

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

        # R-11 (T-63): solo se lee el rasgo si hay una regla red_mulas activa. El rasgo
        # lo materializa D3; aqui no se calcula nada. Sin corrida de D3 es None y la
        # regla queda como no evaluada.
        rasgo_red = None
        if any(r.activa and r.tipo == TIPO_RED_MULAS for r in reglas):
            rasgo_red = await self._rasgos.leer_rasgo(transaccion.cuenta_destino)

        return ContextoEvaluacion(
            perfil=perfil,
            dias_historial=dias_contexto,
            destinos_conocidos=destinos,
            entradas_lista_riesgo=tuple(entradas),
            ventana_origen=ventana,
            rasgo_red=rasgo_red,
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

    # -- Por lote (T-62) ----------------------------------------------------

    async def leer_origenes(
        self, transacciones: Sequence[Transaccion]
    ) -> dict[str, DatosOrigen | Exception]:
        """`leer_origen` de todo el lote en dos lecturas (cuentas y clientes), por `$in`.

        La clave es la cuenta de origen. Una cuenta o un cliente que no existe es el mismo
        `LookupError` de `leer_origen`, devuelto y no lanzado: afecta solo a las
        transacciones de esa cuenta.
        """
        ids = sorted({transaccion.cuenta_origen for transaccion in transacciones})
        cuentas = {
            doc["_id"]: doc async for doc in self._base[CUENTAS].find({"_id": {"$in": ids}})
        }
        clientes_ids = sorted({doc["cliente_id"] for doc in cuentas.values()})
        clientes = {
            doc["_id"]: doc
            async for doc in self._base[CLIENTES].find(
                {"_id": {"$in": clientes_ids}}, {"nombre": 1}
            )
        }
        resultado: dict[str, DatosOrigen | Exception] = {}
        for cuenta_id in ids:
            cuenta = cuentas.get(cuenta_id)
            if cuenta is None:
                resultado[cuenta_id] = LookupError(f"la cuenta de origen {cuenta_id} no existe")
                continue
            cliente = clientes.get(cuenta["cliente_id"])
            if cliente is None:
                resultado[cuenta_id] = LookupError(f"el cliente {cuenta['cliente_id']} no existe")
                continue
            resultado[cuenta_id] = DatosOrigen(
                cuenta_sinpe=cuenta["cuenta_sinpe"],
                cliente_id=cuenta["cliente_id"],
                cliente_nombre=cliente["nombre"],
            )
        return resultado

    async def armar_contextos(
        self,
        transacciones: Sequence[Transaccion],
        origenes: Mapping[str, DatosOrigen],
        reglas: Sequence[Regla],
    ) -> dict[str, ContextoEvaluacion | Exception]:
        """`armar_contexto` de todo el lote. La clave es `transaccion_id`.

        Una lectura por tipo de dato, para el lote entero: perfiles, cuentas de los
        clientes, historial para R-03, historial para R-09, listas de riesgo y rasgos de
        R-11. Despues se cuenta en memoria con la fecha de cada transaccion, con los
        mismos limites de `_destinos_conocidos` y `_ventana_origen`. Si no hay regla de
        destino o de velocidad no se lee su historial: el evaluador no mira ese campo sin
        la regla, igual que hoy.

        Un documento que no valida (perfil, lista, rasgo) se devuelve como excepcion de las
        transacciones que lo usan, no del lote.
        """
        if not transacciones:
            return {}
        dias = _parametro(reglas, TIPO_DESTINO, "dias_historial")
        dias_contexto = max(1, int(dias)) if dias is not None else DIAS_SIN_REGLA
        minutos = _parametro(reglas, TIPO_VELOCIDAD, "ventana_minutos")
        minutos_contexto = max(1, int(minutos)) if minutos is not None else MINUTOS_SIN_REGLA
        con_red = any(r.activa and r.tipo == TIPO_RED_MULAS for r in reglas)

        clientes_ids = sorted({origenes[t.cuenta_origen].cliente_id for t in transacciones})
        destinos = sorted({t.cuenta_destino for t in transacciones})

        perfiles: dict[str, PerfilComportamiento | None | Exception] = dict.fromkeys(
            clientes_ids
        )
        async for documento in self._base[PERFILES].find({"_id": {"$in": clientes_ids}}):
            try:
                perfiles[documento["_id"]] = PerfilComportamiento.model_validate(documento)
            except ValidationError as fallo:
                perfiles[documento["_id"]] = fallo

        listas: dict[str, list[EntradaListaRiesgo] | Exception] = {}
        async for documento in self._base[LISTAS_RIESGO].find({"cuenta": {"$in": destinos}}):
            cuenta = documento["cuenta"]
            try:
                entrada = EntradaListaRiesgo.model_validate(documento)
            except ValidationError as fallo:
                listas[cuenta] = fallo
                continue
            if not isinstance(listas.get(cuenta), Exception):
                listas.setdefault(cuenta, []).append(entrada)  # type: ignore[union-attr]

        rasgos: dict[str, RasgoRed | None | Exception] = {}
        if con_red:
            rasgos = await self._rasgos.leer_rasgos(destinos)

        cuentas_del_cliente: dict[str, set[str]] = {}
        historial_destinos = HistorialPorOrigen()
        if dias is not None:
            cuentas_del_cliente = await self._cuentas_de_clientes(clientes_ids)
            historial_destinos = await self._historial_destinos(
                transacciones, origenes, cuentas_del_cliente, dias_contexto
            )
        historial_ventana = HistorialPorOrigen()
        if minutos is not None:
            historial_ventana = await self._historial_ventana(transacciones, minutos_contexto)

        resultado: dict[str, ContextoEvaluacion | Exception] = {}
        for transaccion in transacciones:
            cliente_id = origenes[transaccion.cuenta_origen].cliente_id
            perfil = perfiles.get(cliente_id)
            entradas = listas.get(transaccion.cuenta_destino, [])
            rasgo = rasgos.get(transaccion.cuenta_destino)
            for dato in (perfil, entradas, rasgo):
                if isinstance(dato, Exception):
                    resultado[transaccion.transaccion_id] = dato
                    break
            else:
                previas = (
                    previas_al_destino(
                        transaccion, cuentas_del_cliente[cliente_id], dias_contexto, historial_destinos
                    )
                    if dias is not None
                    else 0
                )
                ventana = (
                    ventana_de_origen(transaccion, minutos_contexto, historial_ventana)
                    if minutos is not None
                    else VentanaOrigen(
                        ventana_minutos=minutos_contexto,
                        cantidad=1,
                        monto_acumulado_crc=transaccion.monto_crc,
                    )
                )
                resultado[transaccion.transaccion_id] = ContextoEvaluacion(
                    perfil=perfil,  # type: ignore[arg-type]
                    dias_historial=dias_contexto,
                    destinos_conocidos={transaccion.cuenta_destino: previas},
                    entradas_lista_riesgo=tuple(entradas),  # type: ignore[arg-type]
                    ventana_origen=ventana,
                    rasgo_red=rasgo,  # type: ignore[arg-type]
                )
        return resultado

    async def _cuentas_de_clientes(self, clientes_ids: Sequence[str]) -> dict[str, set[str]]:
        """Todas las cuentas de cada cliente, en una lectura (R-03 cuenta por cliente)."""
        cuentas: dict[str, set[str]] = {cliente: set() for cliente in clientes_ids}
        async for documento in self._base[CUENTAS].find(
            {"cliente_id": {"$in": list(clientes_ids)}}, {"cliente_id": 1}
        ):
            cuentas[documento["cliente_id"]].add(documento["_id"])
        return cuentas

    async def _historial_destinos(
        self,
        transacciones: Sequence[Transaccion],
        origenes: Mapping[str, DatosOrigen],
        cuentas_del_cliente: Mapping[str, set[str]],
        dias: int,
    ) -> HistorialPorOrigen:
        """Transferencias de los clientes del lote a los destinos del lote.

        Una clausula por cliente: sus cuentas, sus destinos y el rango de fechas de SUS
        transacciones del lote. Es un superconjunto de lo que cada transaccion necesita;
        el recorte exacto (`[fecha - dias, fecha)`) lo hace `previas_al_destino`. Las
        transacciones del propio lote se agregan aparte: cuentan aunque la lectura no las
        devuelva.
        """
        por_cliente: dict[str, list[Transaccion]] = {}
        for transaccion in transacciones:
            cliente_id = origenes[transaccion.cuenta_origen].cliente_id
            por_cliente.setdefault(cliente_id, []).append(transaccion)
        clausulas = [
            {
                "cuenta_origen": {"$in": sorted(cuentas_del_cliente[cliente_id])},
                "cuenta_destino": {"$in": sorted({t.cuenta_destino for t in propias})},
                "fecha": {
                    "$gte": min(t.fecha for t in propias) - timedelta(days=dias),
                    "$lte": max(t.fecha for t in propias),
                },
            }
            for cliente_id, propias in por_cliente.items()
        ]
        historial = HistorialPorOrigen()
        async for documento in self._transacciones.find(
            {"$or": clausulas}, _PROYECCION_MOVIMIENTO
        ):
            historial.agregar(movimiento_de_documento(documento))
        for transaccion in transacciones:
            historial.agregar(a_movimiento(transaccion))
        return historial

    async def _historial_ventana(
        self, transacciones: Sequence[Transaccion], minutos: int
    ) -> HistorialPorOrigen:
        """Transferencias de los origenes del lote dentro de la ventana de cada una.

        Una clausula por origen, con el rango de fechas de SUS transacciones del lote. La
        rafaga del mismo origen dentro del lote queda en el historial (esta insertada, y
        ademas se agrega aparte) y por eso cuenta en la ventana de las siguientes.
        """
        por_origen: dict[str, list[Transaccion]] = {}
        for transaccion in transacciones:
            por_origen.setdefault(transaccion.cuenta_origen, []).append(transaccion)
        clausulas = [
            {
                "cuenta_origen": origen,
                "fecha": {
                    "$gte": min(t.fecha for t in propias) - timedelta(minutes=minutos),
                    "$lte": max(t.fecha for t in propias),
                },
            }
            for origen, propias in por_origen.items()
        ]
        historial = HistorialPorOrigen()
        async for documento in self._transacciones.find(
            {"$or": clausulas}, _PROYECCION_MOVIMIENTO
        ):
            historial.agregar(movimiento_de_documento(documento))
        for transaccion in transacciones:
            historial.agregar(a_movimiento(transaccion))
        return historial

    async def insertar_alertas(self, alertas: Sequence[Alerta]) -> dict[str, bool | Exception]:
        """`insertar_alerta` de todo el lote: un `insert_many(ordered=False)`.

        La clave es `transaccion_id`; `True` si la alerta es nueva, `False` si la
        transaccion ya tenia una (el reproceso de un lote que se cayo antes del token: lo
        decide el indice unico, no este codigo, y `ordered=False` hace que un duplicado no
        impida insertar el resto). Un duplicado que NO es de esa transaccion (el `_id`
        tomado por otra) se devuelve como excepcion, igual que lo levanta `insertar_alerta`.
        Cualquier otro error de escritura sube y D1 reintenta evento por evento.
        """
        if not alertas:
            return {}
        documentos = [a_entero_64(alerta.a_documento()) for alerta in alertas]
        resultado: dict[str, bool | Exception] = {
            alerta.transaccion_id: True for alerta in alertas
        }
        try:
            await self._alertas.insert_many(documentos, ordered=False)
            return resultado
        except BulkWriteError as fallo:
            errores = fallo.details.get("writeErrors", [])
            if not errores or any(error.get("code") != _CLAVE_DUPLICADA for error in errores):
                raise
            duplicadas = [alertas[error["index"]].transaccion_id for error in errores]
        existentes = {
            documento["transaccion_id"]
            async for documento in self._alertas.find(
                {"transaccion_id": {"$in": duplicadas}}, {"transaccion_id": 1}
            )
        }
        for transaccion_id in duplicadas:
            resultado[transaccion_id] = (
                False
                if transaccion_id in existentes
                else DuplicateKeyError(f"el _id de la alerta de {transaccion_id} ya esta tomado")
            )
        return resultado

    async def leer_token(self, disparador: str) -> Mapping[str, Any] | None:
        documento = await self._estado.find_one({"_id": disparador, "clase": "reanudacion"})
        return documento["token_reanudacion"] if documento is not None else None

    async def medir_rezago(self, disparador: str) -> RezagoMedido:
        """Cuantas transacciones hay sin evaluar y desde cuando (T-57).

        Sin `ultima_transaccion_id` en el estado (D1 nunca proceso una) no hay punto de
        referencia y se informa 0: D1 arranca "desde ahora" y no evalua lo anterior.
        Dos consultas por indice de `_id`, ver `filtro_pendientes`.
        """
        estado = await self._estado.find_one(
            {"_id": disparador, "clase": "reanudacion"}, {"ultima_transaccion_id": 1}
        )
        ultima = estado.get("ultima_transaccion_id") if estado is not None else None
        if ultima is None:
            return RezagoMedido(pendientes=0, mas_vieja=None)
        filtro = filtro_pendientes(ultima)
        pendientes = await self._transacciones.count_documents(filtro)
        if pendientes == 0:
            return RezagoMedido(pendientes=0, mas_vieja=None)
        primera = await self._transacciones.find_one(
            filtro, {"fecha": 1}, sort=[("_id", 1)]
        )
        fecha = primera["fecha"] if primera is not None else None
        if isinstance(fecha, datetime):
            fecha = fecha.replace(tzinfo=UTC) if fecha.tzinfo is None else fecha.astimezone(UTC)
        return RezagoMedido(pendientes=pendientes, mas_vieja=fecha)

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
