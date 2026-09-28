"""Supervision de las tareas de fondo del monolito.

Existe por una falla concreta que un `/salud` normal no ve: el proceso vive, Mongo
contesta, la API responde 200, y el detector lleva dos horas sin procesar una
transaccion. `/salud` diria "ok" todo el tiempo. Esta carpeta es lo que hace posible
que `/estado` diga la verdad.

Tres piezas, y ninguna sabe que existe una pantalla (la misma regla que
`app/deteccion/`; hay una prueba de arquitectura que lo verifica):

- `registro.py`: el `Supervisor`. Lanza las tareas del lifespan, las vigila y arma el
  informe. No consulta Mongo y no importa la capa HTTP.
- `latido.py`: la tarea real que hoy se supervisa. Es el pulso de la plataforma.
- `DISPARADORES_PREVISTOS`: el catalogo de los disparadores que todavia no existen
  (D1, D2, D3), con la historia que trae cada uno. Se reportan como
  `no_construida`, no se simulan: un disparador falso hoy es un borrado manana.

Como se engancha un disparador cuando exista (H-09, H-11, H-22): en el lifespan,

    supervisor.lanzar("d1_deteccion", d1(base, supervisor))

y dentro del bucle del disparador, por cada evento procesado,

    supervisor.latir("d1_deteccion")

Nada mas. El nombre tiene que ser una clave de `DISPARADORES_PREVISTOS` para que
`/estado` lo reconozca como el disparador que estaba prometido.
"""

from __future__ import annotations

from app.supervision.latido import NOMBRE_LATIDO, latido
from app.supervision.registro import (
    DISPARADORES_PREVISTOS,
    ErrorRegistrado,
    EstadoSupervision,
    EstadoTarea,
    InformeSupervision,
    InformeTarea,
    Supervisor,
)

__all__ = [
    "DISPARADORES_PREVISTOS",
    "NOMBRE_LATIDO",
    "ErrorRegistrado",
    "EstadoSupervision",
    "EstadoTarea",
    "InformeSupervision",
    "InformeTarea",
    "Supervisor",
    "latido",
]
