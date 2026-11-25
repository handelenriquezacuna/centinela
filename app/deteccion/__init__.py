"""Motor de deteccion de Centinela.

Headless a proposito: no importa `app/api/` (lo verifica
`pruebas/arquitectura/prueba_capas.py`). El evaluador (H-08) es una funcion pura;
los disparadores (H-09 en adelante) son los que hablan con la base, siempre a
traves de `app/repos/`.

D1 no se reexporta aqui: se importa de `app.deteccion.d1`. `app/repos/deteccion.py`
importa el evaluador de este paquete, y si este `__init__` importara D1 (que importa
ese repositorio) quedaria un ciclo de importacion.
"""

from app.deteccion.evaluador import (
    ContextoEvaluacion,
    CorteSeveridad,
    Evaluacion,
    PoliticaDeteccion,
    RasgoRed,
    ReglaDisparada,
    ReglaNoEvaluada,
    VentanaOrigen,
    evaluar,
)

__all__ = [
    "ContextoEvaluacion",
    "CorteSeveridad",
    "Evaluacion",
    "PoliticaDeteccion",
    "RasgoRed",
    "ReglaDisparada",
    "ReglaNoEvaluada",
    "VentanaOrigen",
    "evaluar",
]
