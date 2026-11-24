"""Formatos de Costa Rica para el portal: un solo lugar, usado por todas las plantillas.

La base guarda dinero como entero y fechas en UTC; aca se traduce al presentar:

- montos: `₡750.000` (punto de miles, nunca centimos: SINPE Movil no los opera);
- fechas: `DD/MM/AAAA HH:MM` en hora de Costa Rica;
- porcentajes y decimales: coma decimal (`11,2%`);
- claves internas legibles: `en_revision` se muestra `En revisión`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from jinja2 import Environment

from app.modelos import ZONA_COSTA_RICA

_ETIQUETAS = {
    "nueva": "Nueva",
    "en_revision": "En revisión",
    "confirmada": "Confirmada",
    "descartada": "Descartada",
    "escalada": "Escalada",
    "sinpe_movil": "SINPE Móvil",
    "sin_asignar": "Sin asignar",
    "CRITICA": "Crítica",
    "ALTA": "Alta",
    "MEDIA": "Media",
    "BAJA": "Baja",
}


def hora_costa_rica(valor: Any) -> str:
    """Fecha y hora local como `24/10/2026 06:00`. Vacio si no es una fecha."""
    if not isinstance(valor, datetime):
        return ""
    return valor.astimezone(ZONA_COSTA_RICA).strftime("%d/%m/%Y %H:%M")


def colones(valor: Any) -> str:
    """Colones enteros como `₡750.000`. Vacio si no es un entero."""
    if not isinstance(valor, int) or isinstance(valor, bool):
        return ""
    signo = "-" if valor < 0 else ""
    return f"{signo}₡{abs(valor):,}".replace(",", ".")


def decimal(valor: Any, decimales: int = 1) -> str:
    """Un numero con coma decimal: `11,2`."""
    if not isinstance(valor, (int, float)) or isinstance(valor, bool):
        return ""
    return f"{valor:.{decimales}f}".replace(".", ",")


def porcentaje(valor: Any, decimales: int = 1) -> str:
    """Un porcentaje con coma decimal: `12,5%`. El valor ya viene en porcentaje (12.5)."""
    texto = decimal(valor, decimales)
    return f"{texto}%" if texto else ""


def etiqueta(valor: Any) -> str:
    """Estado o clave interna en palabras: `en_revision` -> `En revisión`."""
    if valor is None or valor == "":
        return ""
    clave = str(valor)
    if clave in _ETIQUETAS:
        return _ETIQUETAS[clave]
    palabras = clave.replace("_", " ")
    return palabras[:1].upper() + palabras[1:]


def antiguedad_legible(segundos: float | None) -> str:
    """`125.0` -> `2 min`; el aviso dice cuanto falta para que D1 se ponga al dia."""
    if segundos is None:
        return ""
    total = max(1, int(segundos))
    if total < 60:
        return f"{total} s"
    if total < 3600:
        return f"{total // 60} min"
    if total < 86400:
        return f"{total // 3600} h"
    return f"{total // 86400} d"


def registrar(entorno: Environment) -> None:
    """Instala los filtros en un entorno de Jinja."""
    entorno.filters["hora_costa_rica"] = hora_costa_rica
    entorno.filters["colones"] = colones
    entorno.filters["decimal"] = decimal
    entorno.filters["porcentaje"] = porcentaje
    entorno.filters["etiqueta"] = etiqueta
