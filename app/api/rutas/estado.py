"""Ruta de estado funcional.

`/salud` y `/estado` son rutas distintas porque responden preguntas distintas, y
mezclarlas esconde la falla que mas caro sale:

| Ruta | Pregunta | Quien la usa |
|---|---|---|
| `GET /salud` | El proceso vive y la base contesta | Un orquestador, para decidir si reinicia |
| `GET /estado` | El sistema esta haciendo su trabajo | Una persona, antes de confiar en la pantalla |

Tener conexion con Mongo no demuestra que el detector siga procesando. Un solo
endpoint de salud responde 200 mientras no se detecta un solo fraude, y esa es
exactamente la falla que arruina una demo: todo parece bien y la cola no crece.

`/estado` responde 200 aunque el sistema este degradado, igual que `/salud`. La razon
es la misma: la ruta cumplio su trabajo respondiendo, y quien la consulta necesita el
diagnostico, no un codigo de error sin explicacion. El diagnostico va en el cuerpo,
en `estado`.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.dependencias import AjustesDep, SupervisorDep
from app.api.errores import respuestas_documentadas
from app.api.esquemas import EstadoFuncional

enrutador = APIRouter(tags=["salud"])


@enrutador.get(
    "/estado",
    response_model=EstadoFuncional,
    summary="Estado funcional: tareas de fondo, ultimo evento, retraso y ultimo error",
    responses=respuestas_documentadas(status.HTTP_500_INTERNAL_SERVER_ERROR),
)
async def estado(ajustes: AjustesDep, supervisor: SupervisorDep) -> EstadoFuncional:
    """Informe de las tareas de fondo del proceso.

    No consulta la base: lee lo que el supervisor ya sabe. Es deliberado y es la
    diferencia con `/salud`. Si `/estado` preguntara a Mongo en el momento de la
    peticion, volveria a responder "la base contesta" y no "el detector esta
    procesando", que es lo unico que esta ruta viene a decir. El que habla con la base
    es el latido, cada `supervision.intervalo_latido_segundos`, y lo que se publica
    aqui es cuando fue la ultima vez que termino su trabajo.
    """
    from app import VERSION

    return EstadoFuncional.desde_informe(
        supervisor.informe(), version=VERSION, entorno=ajustes.app.entorno
    )
