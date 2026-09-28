"""Ruta de salud.

`/salud` y `/estado` son rutas DISTINTAS y responden preguntas distintas:

- `/salud` (esta): el proceso esta vivo y la base contesta. Es lo que mira un
  orquestador para decidir si reinicia el contenedor.
- `/estado` (H-00B): el sistema esta haciendo su trabajo. Reporta los disparadores
  D1/D2/D3, el ultimo evento procesado, el retraso y el ultimo error; matar una tarea
  a mano lo deja en `degradado`.

Estan separadas porque un proceso vivo con el motor de deteccion caido es exactamente
el escenario que un solo endpoint de salud esconde: responde 200 mientras no se
detecta un solo fraude. No se implementa `/estado` en H-00A para no publicar una
forma que ninguna prueba sostiene todavia.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.dependencias import AjustesDep, BaseDep
from app.api.errores import respuestas_documentadas
from app.api.esquemas import Salud
from app.repos.salud import estado_conexion

enrutador = APIRouter(tags=["salud"])


@enrutador.get(
    "/salud",
    response_model=Salud,
    summary="Vivacidad del proceso y estado de la conexion a MongoDB",
    responses=respuestas_documentadas(status.HTTP_500_INTERNAL_SERVER_ERROR),
)
async def salud(ajustes: AjustesDep, base: BaseDep) -> Salud:
    """Responde siempre 200 mientras el proceso este vivo.

    Un fallo de la base se reporta en el cuerpo (`mongo_conectado: false`) y no como
    error HTTP, a proposito: la ruta de salud cumplio su trabajo respondiendo, y
    quien la consulta necesita el diagnostico, no un 503 sin explicacion.
    """
    from app import VERSION

    conexion = await estado_conexion(base)

    return Salud(
        estado="ok" if conexion["conectado"] else "degradado",
        version=VERSION,
        entorno=ajustes.app.entorno,
        mongo_conectado=conexion["conectado"],
        replica_set=conexion["replica_set"],
        es_primario=conexion["es_primario"],
        detalle=conexion["detalle"],
    )
