"""Consultas sobre `alertas`: la cola del panel (H-13) y resolver una alerta (H-14).

`construir_filtro` y `planear_transicion` estan separadas de la consulta a proposito:
son funciones puras, se prueban sin base de datos y son donde de verdad se puede meter
un error silencioso. El doble de `pruebas/dobles/` usa las mismas.

Resolver una alerta (H-14), en una sola escritura condicionada:

- `planear_transicion` valida contra la maquina de estados (`exigir_transicion`) y
  arma un filtro con el estado y el agente LEIDOS. Si otro agente la movio en el medio,
  el filtro no encuentra nada, no se escribe nada y la ruta responde 409.
- El historial se AGREGA con `$push`; nunca se reescribe la lista.
- Escalar crea el caso y mueve la alerta en la MISMA transaccion multi-documento: si
  cualquiera de las dos escrituras falla, no queda ni el caso ni el cambio de estado.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.modelos import (
    COLECCION_ALERTAS,
    COLECCION_CASOS,
    Caso,
    EstadoAlerta,
    EstadoCaso,
    Severidad,
    exigir_transicion,
)

# Orden del panel (D-20): lo mas riesgoso primero, luego lo mas reciente, y el
# identificador como desempate. Se ordena por puntaje y no por severidad porque la
# severidad es texto (orden alfabetico inutil) y sale del puntaje por cortes crecientes
# de politica_deteccion: a mas puntaje, igual o mayor severidad. Asi el orden lo hace
# la base con indice (scripts/semilla/02-indices.js) y no Python sobre la pagina. Sin
# el desempate por _id, dos alertas iguales pueden salir en distinto orden entre
# paginas y el agente ve un documento dos veces o ninguna.
ORDEN_PANEL: list[tuple[str, int]] = [
    ("puntaje", DESCENDING),
    ("fecha_creacion", DESCENDING),
    ("_id", ASCENDING),
]


def construir_filtro(
    estado: EstadoAlerta | None = None,
    severidad: Severidad | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
) -> dict[str, Any]:
    """Filtro de Mongo a partir de los parametros del panel.

    Los filtros son combinables: cada uno que llega agrega una condicion. Ninguno
    que llega vacio agrega nada, para que la consulta sin filtros siga pudiendo usar
    el indice del panel.

    El rango de fecha se arma sobre `fecha_creacion` y es cerrado por abajo y
    abierto por arriba (`$gte` y `$lte` es lo intuitivo para un agente que pide "del
    1 al 15"; se usa `$lte` a proposito).
    """
    filtro: dict[str, Any] = {}

    if estado is not None:
        filtro["estado"] = EstadoAlerta(estado).value
    if severidad is not None:
        filtro["severidad"] = Severidad(severidad).value

    rango: dict[str, datetime] = {}
    if desde is not None:
        rango["$gte"] = desde
    if hasta is not None:
        rango["$lte"] = hasta
    if rango:
        filtro["fecha_creacion"] = rango

    return filtro


# ---------------------------------------------------------------------------
# H-14 · Transiciones
# ---------------------------------------------------------------------------

PREFIJO_ALERTA = "ALR-"
PREFIJO_CASO = "CAS-"


class ConflictoAlerta(Exception):
    """La transicion es valida en la tabla pero no se puede aplicar a esta alerta ahora:
    la tomo otro agente, cambio mientras se resolvia o su caso ya existe. Es un 409."""

    def __init__(self, mensaje: str, *, estado_actual: str | None = None) -> None:
        super().__init__(mensaje)
        self.estado_actual = estado_actual


def caso_id_para(alerta_id: str) -> str:
    """`ALR-005` -> `CAS-005`. El caso de una escalacion sale de la alerta.

    Derivado, por la misma razon que `alerta_id_para` en D1: una alerta se escala a lo
    sumo una vez (`escalada` es terminal), asi que no hace falta un contador con su
    escritura extra y su carrera. Si el `_id` ya esta tomado, el indice lo dice y la
    transaccion entera se deshace.
    """
    if not alerta_id.startswith(PREFIJO_ALERTA):
        raise ValueError(f"identificador de alerta inesperado: {alerta_id}")
    return PREFIJO_CASO + alerta_id.removeprefix(PREFIJO_ALERTA)


@dataclass
class CasoDeLote:
    """El caso que une a las alertas escaladas de un mismo lote (T-56, D-57).

    `caso_id` es `None` hasta que la PRIMERA escalacion del lote se aplica: esa abre el
    caso, en la misma transaccion que mueve su alerta. Las siguientes lo reutilizan.
    Como el caso nace dentro de la transaccion de una alerta que si pasa, un lote donde
    todas fallan no deja un caso vacio, y una alerta que falla no queda en el caso.
    """

    caso_id: str | None = None


@dataclass(frozen=True)
class PlanTransicion:
    """Lo que hay que escribir para mover una alerta. Puro: no toca la base."""

    alerta_id: str
    origen: EstadoAlerta
    destino: EstadoAlerta
    # Filtro condicionado: estado y agente tal como se leyeron.
    filtro: dict[str, Any]
    # `$set` del estado (y del agente al tomarla) y `$push` al historial.
    actualizacion: dict[str, Any]
    # Documento del caso, solo al escalar.
    caso: dict[str, Any] | None
    # Caso ya abierto por otra alerta del mismo lote: esta alerta se agrega a el.
    agregar_a_caso: str | None = None


def planear_transicion(
    actual: Mapping[str, Any],
    *,
    destino: EstadoAlerta,
    agente_id: str,
    nota: str,
    ahora: datetime,
    lote: CasoDeLote | None = None,
) -> PlanTransicion:
    """Valida y arma la escritura. Lanza `TransicionInvalida` o `ConflictoAlerta`.

    - Tomar (`nueva -> en_revision`) asigna `agente_id`.
    - Resolver (`en_revision -> confirmada/descartada/escalada`) solo lo puede hacer el
      agente que la tomo: si es otro, conflicto. El agente
      es el de la sesion (H-25), no uno que mande el cliente.
    - Con `lote`, escalar abre UN caso por cuenta destino para todo el lote: la primera
      alerta lo crea (con la cuenta y el agente) y las demas se agregan a el. Sin `lote`
      la escalacion es la de siempre, un caso por alerta.
    """
    alerta_id = str(actual["_id"])
    origen = EstadoAlerta(actual["estado"])
    destino = EstadoAlerta(destino)
    exigir_transicion(origen, destino)

    duenio = actual.get("agente_id")
    if origen == EstadoAlerta.EN_REVISION and duenio != agente_id:
        raise ConflictoAlerta(
            f"La alerta {alerta_id} la tiene en revision {duenio}; solo ese agente la resuelve",
            estado_actual=origen.value,
        )

    filtro: dict[str, Any] = {
        "_id": alerta_id,
        "estado": origen.value,
        "agente_id": duenio if duenio is not None else {"$exists": False},
    }
    cambios: dict[str, Any] = {"estado": destino.value}
    if origen == EstadoAlerta.NUEVA:
        cambios["agente_id"] = agente_id
    entrada = {"accion": destino.value, "agente_id": agente_id, "fecha": ahora, "nota": nota}

    caso = None
    agregar_a_caso = None
    if destino == EstadoAlerta.ESCALADA and lote is not None and lote.caso_id is not None:
        agregar_a_caso = lote.caso_id
    elif destino == EstadoAlerta.ESCALADA:
        caso = Caso(
            _id=caso_id_para(alerta_id),
            alertas=[alerta_id],
            estado=EstadoCaso.INVESTIGACION,
            fecha_apertura=ahora,
            cuenta_destino=actual["cuenta_destino"] if lote is not None else None,
            agente_id=agente_id if lote is not None else None,
        ).a_documento()

    return PlanTransicion(
        alerta_id=alerta_id,
        origen=origen,
        destino=destino,
        filtro=filtro,
        actualizacion={"$set": cambios, "$push": {"historial": entrada}},
        caso=caso,
        agregar_a_caso=agregar_a_caso,
    )


class _SinCoincidencia(Exception):
    """Interna: el filtro condicionado no encontro la alerta dentro de la transaccion."""


@runtime_checkable
class ProtocoloRepoAlertas(Protocol):
    """Lo que la API necesita de este repositorio.

    Existe para que las pruebas de contrato de la API puedan inyectar un doble con
    `dependency_overrides` y correr sin Mongo arriba. Si el metodo cambia de forma,
    el doble deja de cumplir el protocolo y la prueba lo dice.
    """

    async def listar(
        self,
        *,
        estado: EstadoAlerta | None,
        severidad: Severidad | None,
        desde: datetime | None,
        hasta: datetime | None,
        limite: int,
        desplazamiento: int,
    ) -> tuple[list[dict[str, Any]], int]: ...

    async def obtener(self, alerta_id: str) -> dict[str, Any] | None: ...

    async def aplicar_transicion(self, plan: PlanTransicion) -> dict[str, Any]: ...


class RepoAlertas:
    """Acceso a la coleccion `alertas` (y a `casos`, solo para escalar)."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._base = base
        self._coleccion = base[COLECCION_ALERTAS]
        self._casos = base[COLECCION_CASOS]

    async def listar(
        self,
        *,
        estado: EstadoAlerta | None = None,
        severidad: Severidad | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 50,
        desplazamiento: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Pagina de alertas y total que cumple el filtro.

        El total se cuenta con el mismo filtro y no con el de la pagina: el agente
        necesita saber cuantas alertas hay en su cola, no cuantas caben en la
        pantalla.
        """
        filtro = construir_filtro(
            estado=estado, severidad=severidad, desde=desde, hasta=hasta
        )

        total = await self._coleccion.count_documents(filtro)

        cursor = (
            self._coleccion.find(filtro)
            .sort(ORDEN_PANEL)
            .skip(desplazamiento)
            .limit(limite)
        )
        documentos = await cursor.to_list(length=limite)

        return documentos, total

    async def obtener(self, alerta_id: str) -> dict[str, Any] | None:
        return await self._coleccion.find_one({"_id": alerta_id})

    async def aplicar_transicion(self, plan: PlanTransicion) -> dict[str, Any]:
        """Escribe el plan y devuelve la alerta como quedo. `ConflictoAlerta` si no se pudo.

        Sin caso es una sola escritura atomica de un documento: no hace falta sesion.
        Con caso, `with_transaction`: inserta el caso y DESPUES mueve la alerta; si la
        alerta ya no esta como se leyo, se lanza dentro de la transaccion y el caso se
        deshace con ella. `with_transaction` reintenta solo los errores transitorios.
        """
        if plan.agregar_a_caso is not None:
            return await self._agregar_a_caso(plan)
        if plan.caso is None:
            actualizada = await self._coleccion.find_one_and_update(
                plan.filtro, plan.actualizacion, return_document=ReturnDocument.AFTER
            )
            if actualizada is None:
                raise self._carrera(plan)
            return actualizada

        caso = plan.caso

        async def escalar(sesion: AsyncClientSession) -> dict[str, Any]:
            await self._casos.insert_one(caso, session=sesion)
            actualizada = await self._coleccion.find_one_and_update(
                plan.filtro,
                plan.actualizacion,
                return_document=ReturnDocument.AFTER,
                session=sesion,
            )
            if actualizada is None:
                raise _SinCoincidencia
            return actualizada

        try:
            async with self._base.client.start_session() as sesion:
                return await sesion.with_transaction(escalar)
        except _SinCoincidencia:
            raise self._carrera(plan) from None
        except DuplicateKeyError as fallo:
            raise ConflictoAlerta(
                f"Ya existe el caso {caso['_id']}; la alerta {plan.alerta_id} no se escalo",
                estado_actual=plan.origen.value,
            ) from fallo

    async def _agregar_a_caso(self, plan: PlanTransicion) -> dict[str, Any]:
        """Mueve la alerta y la agrega a un caso ya abierto, en una sola transaccion.

        Si la alerta cambio en el medio, o el caso no esta en investigacion, se lanza
        dentro de la transaccion: ni la alerta se mueve ni el caso la recibe.
        """
        caso_id = plan.agregar_a_caso

        async def agregar(sesion: AsyncClientSession) -> dict[str, Any]:
            actualizada = await self._coleccion.find_one_and_update(
                plan.filtro,
                plan.actualizacion,
                return_document=ReturnDocument.AFTER,
                session=sesion,
            )
            if actualizada is None:
                raise _SinCoincidencia
            recibido = await self._casos.update_one(
                {"_id": caso_id, "estado": EstadoCaso.INVESTIGACION.value},
                {"$addToSet": {"alertas": plan.alerta_id}},
                session=sesion,
            )
            if recibido.matched_count == 0:
                raise ConflictoAlerta(
                    f"El caso {caso_id} ya no esta en investigacion; la alerta "
                    f"{plan.alerta_id} no se escalo",
                    estado_actual=plan.origen.value,
                )
            return actualizada

        try:
            async with self._base.client.start_session() as sesion:
                return await sesion.with_transaction(agregar)
        except _SinCoincidencia:
            raise self._carrera(plan) from None

    def _carrera(self, plan: PlanTransicion) -> ConflictoAlerta:
        return ConflictoAlerta(
            f"La alerta {plan.alerta_id} cambio mientras se resolvia (otro agente la "
            "tomo o la resolvio); volver a leerla",
            estado_actual=None,
        )
