"""Fragmentos HTML que viajan por el canal SSE.

Por que el canal manda HTML y no JSON: el panel es Jinja + HTMX, y HTMX inserta en el
DOM lo que le llega. Si el evento trajera JSON, el navegador tendria que armar la fila
con JavaScript, que es exactamente el trabajo que HTMX evita. El precio es que el
servidor decide la forma de la fila; a cambio, el portal no tiene una capa de
plantillas duplicada en JavaScript.

**Alcance de H-00B, dicho claro:** aqui hay una sola plantilla, la fila del panel, y
es provisional en su marcado. Lo que H-00B fija y nadie mas puede cambiar sin avisar es
el *contrato del canal* (nombre del evento, `id` = token de reanudacion, cuerpo = un
fragmento HTML listo para insertar). El marcado de la fila es de H-17, que la va a
reescribir con las clases y las columnas de su diseno. Mientras eso llegue, el
fragmento alcanza para probar el canal de punta a punta con datos reales.

El entorno de Jinja se crea una sola vez, al importar el modulo: compilar plantillas
por peticion es gasto puro, y el canal emite un fragmento por alerta.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.modelos import ZONA_COSTA_RICA

# app/api/fragmentos.py -> app/api -> app
RUTA_PLANTILLAS = Path(__file__).resolve().parents[1] / "plantillas"

PLANTILLA_FILA_ALERTA = "sse/fila_alerta.html"

# `autoescape` encendido no es cosmetico: `cliente_nombre` es dato que el sistema no
# controla, y el fragmento se inserta en el DOM tal cual. Sin escape, un nombre con
# `<script>` seria una inyeccion en el panel del agente.
ENTORNO = Environment(
    loader=FileSystemLoader(str(RUTA_PLANTILLAS)),
    autoescape=select_autoescape(default_for_string=True, default=True),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _hora_costa_rica(valor: Any) -> str:
    """Hora local de Costa Rica para mostrar. La base guarda UTC; el portal traduce."""
    if not isinstance(valor, datetime):
        return ""
    return valor.astimezone(ZONA_COSTA_RICA).strftime("%Y-%m-%d %H:%M")


def _colones(valor: Any) -> str:
    """Colones enteros con separador de miles. Nunca centimos: SINPE no los opera."""
    if not isinstance(valor, int):
        return ""
    return f"{valor:,}".replace(",", " ")


ENTORNO.filters["hora_costa_rica"] = _hora_costa_rica
ENTORNO.filters["colones"] = _colones


def fragmento_alerta(documento: Mapping[str, Any], *, operacion: str) -> str:
    """HTML de una fila del panel, en una sola linea.

    Una sola linea a proposito: SSE parte un cuerpo con saltos de linea en varios
    `data:` y el navegador los vuelve a unir con `\\n`. Funciona, pero deja el flujo
    ilegible cuando hay que depurarlo con `curl`, y no aporta nada.

    `operacion` viaja como atributo (`data-operacion`) porque el panel necesita
    distinguir una alerta nueva de una que cambio de estado: la primera se agrega
    arriba de la cola, la segunda reemplaza la fila que ya estaba.
    """
    plantilla = ENTORNO.get_template(PLANTILLA_FILA_ALERTA)
    renderizado = plantilla.render(alerta=documento, operacion=operacion)
    return " ".join(renderizado.split())
