"""El latido: la tarea de fondo que hoy se supervisa de verdad.

Por que existe una tarea real en H-00B, si los tres disparadores son de H-09, H-11 y
H-22: porque un andamio de supervision sin nada que supervisar no se puede probar.
El criterio de la historia pide que matar una tarea a mano deje `/estado` en
`degradado`, y para eso hace falta una tarea que se pueda matar.

Y por que ESTA tarea y no un disparador de mentira: el latido hace trabajo real que el
sistema necesita igual. Comprueba, sin que nadie pregunte, que la base sigue
contestando, y deja el resultado en el informe con su marca de tiempo. La diferencia
con `/salud` es quien pregunta: `/salud` mira la base en el momento en que alguien
abre la ruta; el latido la mira solo, cada `intervalo_segundos`, asi que `/estado`
puede decir "la base contesto hace 3 segundos" sin haber consultado nada en esta
peticion. Cuando D1 exista, el latido no estorba ni se borra: sigue siendo el pulso de
la plataforma.

Un fallo de la base NO mata la tarea: se anota como ultimo error y se sigue
intentando. Lo que degrada `/estado` no es el error, es dejar de latir: si la base no
vuelve, el latido no avanza, el retraso pasa la tolerancia y la tarea queda
`retrasada`, que es la verdad que interesa.

El latido no recibe la base, recibe una comprobacion (`comprobar`). Dos razones: este
modulo no importa el driver -asi la regla "pymongo solo en app/repos/" sigue siendo
cierta sin excepciones nuevas- y la tarea se puede probar con una comprobacion falsa,
sin Mongo arriba.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.supervision.registro import Supervisor

registro_log = logging.getLogger("centinela.supervision")

# El nombre con el que el latido aparece en `/estado`. No esta en
# DISPARADORES_PREVISTOS porque no es D1, D2 ni D3: es el pulso de la plataforma.
NOMBRE_LATIDO = "latido"

# Lo que el latido necesita saber hacer: una comprobacion que no devuelve nada y que
# lanza si algo anda mal. `app/repos/salud.py` la provee sobre la base de Mongo.
Comprobacion = Callable[[], Awaitable[None]]


async def latido(
    comprobar: Comprobacion,
    supervisor: Supervisor,
    *,
    intervalo_segundos: float,
    nombre: str = NOMBRE_LATIDO,
) -> None:
    """Bucle infinito: comprueba, late y espera.

    El orden importa. Se comprueba primero y se espera despues, para que el primer
    latido ocurra al arrancar y no `intervalo_segundos` mas tarde: si no, `/estado`
    arrancaria sin un solo evento procesado y habria que explicar por que.

    `asyncio.CancelledError` se deja pasar sin tocarla. Atraparla para "limpiar" es el
    error clasico que convierte una cancelacion en una tarea que no muere, y entonces
    el lifespan se queda colgado al apagar.
    """
    while True:
        try:
            await comprobar()
            supervisor.latir(nombre)
        except asyncio.CancelledError:
            raise
        except Exception as fallo:  # noqa: BLE001 - el latido nunca tumba el proceso
            supervisor.anotar_error(nombre, fallo)
            registro_log.warning("latido con error: %s", fallo)

        await asyncio.sleep(intervalo_segundos)
