"""Motor de deteccion de Centinela.

Headless a proposito: no importa `app/api/` (lo verifica
`pruebas/arquitectura/prueba_capas.py`). El evaluador (H-08) es una funcion pura;
los disparadores (H-09 en adelante) son los que hablan con la base, siempre a
traves de `app/repos/`.
"""

from app.deteccion.evaluador import (
    ContextoEvaluacion,
    CorteSeveridad,
    Evaluacion,
    PoliticaDeteccion,
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
    "ReglaDisparada",
    "ReglaNoEvaluada",
    "VentanaOrigen",
    "evaluar",
]
