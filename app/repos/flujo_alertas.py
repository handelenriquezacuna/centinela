"""El change stream de `alertas`: la fuente unica del canal en vivo.

Esta es la mitad de abajo del canal SSE. La de arriba, la que arma los eventos, esta
en `app/api/canal.py`. Aqui vive la consulta, porque ninguna consulta va fuera de
`app/repos/` y `watch()` es una consulta.

**No hay bus en memoria, y es la decision central.** El canal observa la coleccion
`alertas`, no una cola de proceso. Consecuencia practica: da exactamente igual quien
escribio la alerta -`tareas.py datos-demo`, D1 dentro de este mismo proceso, un script
por linea de comandos, o `mongosh` a mano-, porque lo que se observa es lo persistido.
Un bus en memoria solo habria visto las alertas que nacieron dentro del proceso, y la
demo de la alerta insertada a mano no existiria.

**El pipeline y las opciones se declaran una sola vez** (`PIPELINE_ALERTAS`,
`OPCIONES_FLUJO`) y se usan igual al abrir y al reanudar. Reanudar con otro pipeline u
otras opciones da comportamiento impredecible: el servidor aplicaria el pipeline nuevo
a un punto del oplog que se eligio con el viejo. Estan en constantes justamente para
que las dos aperturas no puedan separarse.

**Limite conocido, asumido a proposito:** un change stream por conexion. A la escala
del proyecto (un panel, unos pocos agentes) es lo correcto y es lo simple. Si algun dia
hicieran falta cientos de paneles, el cambio seria un solo observador del proceso
repartiendo a las conexiones; no se construye hoy porque hoy no resuelve un problema
que exista.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure

from app.modelos import COLECCION_ALERTAS

registro_log = logging.getLogger("centinela.flujo")

# Que cambios interesan al panel. `insert` es la alerta nueva; `update` y `replace`
# son la alerta que cambio de estado (H-14), que tambien tiene que verse sola en la
# pantalla del agente. `delete` no entra: una alerta no se borra, se resuelve.
PIPELINE_ALERTAS: list[dict[str, Any]] = [
    {"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}
]

# `updateLookup` hace que un `update` traiga el documento completo tal como quedo, y
# no solo los campos que cambiaron. El panel pinta la fila entera, asi que sin esto
# cada actualizacion obligaria a una consulta extra por alerta.
OPCIONES_FLUJO: dict[str, Any] = {"full_document": "updateLookup"}

# El caso canonico de token inservible: el oplog dio la vuelta y el punto de
# reanudacion ya no esta. Es el que el mensaje nombra; la regla de abajo es mas amplia.
CHANGE_STREAM_HISTORY_LOST = 286


class TokenDeReanudacionInvalido(Exception):
    """El token que trajo el cliente no sirve para reanudar.

    Lo levanta esta capa y lo traduce `app/api/canal.py` en el evento
    `resincronizar`. El motivo viaja en el mensaje para que quede en el registro.
    """


@dataclass(frozen=True)
class CambioAlerta:
    """Un cambio observado en `alertas`, ya sin la forma del driver.

    `token` es el `_id` del evento del change stream: el token de reanudacion. El
    canal lo publica como `id` del evento SSE, y es lo que el navegador devuelve en
    `Last-Event-ID` al reconectar.
    """

    token: Mapping[str, Any]
    operacion: str
    documento: Mapping[str, Any] | None


@runtime_checkable
class ProtocoloFlujoAlertas(Protocol):
    """Lo que el canal SSE necesita de este repositorio.

    Existe por la misma razon que `ProtocoloRepoAlertas`: que las pruebas del canal
    puedan inyectar un doble y ejercitar la reanudacion, el token inservible y la
    forma del evento sin Mongo arriba. Y tiene un efecto de capas que importa:
    `app/api/` no necesita nombrar el tipo del driver en ninguna firma.
    """

    def observar(
        self, *, desde_token: Mapping[str, Any] | None = None
    ) -> AsyncIterator[CambioAlerta]: ...


class RepoFlujoAlertas:
    """Observador de `alertas` por change stream."""

    def __init__(self, base: AsyncDatabase) -> None:
        self._coleccion = base[COLECCION_ALERTAS]

    async def observar(
        self, *, desde_token: Mapping[str, Any] | None = None
    ) -> AsyncIterator[CambioAlerta]:
        """Itera los cambios de `alertas`, opcionalmente reanudando desde un token.

        Se abre con `resumeAfter` y no con `startAfter`: el panel quiere seguir
        despues del ultimo evento que ya pinto.

        Sobre el manejo de errores, que es la parte delicada:

        - pymongo reintenta solo los errores reanudables (una eleccion de primario, un
          corte de red) con su propio token interno, y en ese caso aqui no se ve nada.
          Esa es la razon de no escribir un reintento a mano: duplicaria el del driver
          y los dos pelearian por el mismo cursor.
        - Un `OperationFailure` al abrir CON token se trata como token inservible. La
          regla es amplia a proposito: `ChangeStreamHistoryLost` (286) es el caso
          canonico -el oplog dio la vuelta-, pero un token corrupto, truncado o de
          otro despliegue da codigos distintos (9, 15, 40649, 50811, 50816 segun como
          este roto) y todos significan lo mismo para el panel: este token no sirve,
          pedi la cola de nuevo.
        - Si el fallo aparece sin token, o despues de haber entregado eventos, no es un
          problema del token y sube tal cual: es un error de verdad.
        """
        entregados = 0
        try:
            flujo = await self._coleccion.watch(
                PIPELINE_ALERTAS, resume_after=desde_token, **OPCIONES_FLUJO
            )
            async with flujo:
                async for cambio in flujo:
                    entregados += 1
                    yield CambioAlerta(
                        token=cambio["_id"],
                        operacion=cambio["operationType"],
                        documento=cambio.get("fullDocument"),
                    )
        except OperationFailure as fallo:
            if desde_token is not None and entregados == 0:
                etiqueta = (
                    "ChangeStreamHistoryLost"
                    if fallo.code == CHANGE_STREAM_HISTORY_LOST
                    else f"codigo {fallo.code}"
                )
                registro_log.info("token de reanudacion inservible (%s)", etiqueta)
                raise TokenDeReanudacionInvalido(
                    f"el token de reanudacion ya no sirve ({etiqueta})"
                ) from fallo
            raise
