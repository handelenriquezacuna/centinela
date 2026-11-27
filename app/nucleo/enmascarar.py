"""Enmascarado de datos personales para mostrarlos (T-82).

Funciones puras. El banco no muestra un telefono ni una cuenta completos donde no hace
falta: el portal del agente ve el telefono del cliente como `8***-**55`, y el portal del
cliente ve el destino como `6033-****`. El dato completo queda en la base; esto solo
decide que se pinta.

Formato de ambos: cuatro digitos, guion, cuatro digitos (`8712-4455`). Un valor que no
cumple se enmascara entero (`****-****`) en vez de dejar pasar algo que no se entiende.
"""

from __future__ import annotations

import re

_FORMATO = re.compile(r"^(\d{4})-(\d{4})$")
TOTALMENTE_ENMASCARADO = "****-****"


def telefono(valor: str | None) -> str | None:
    """`8712-4455` -> `8***-**55`: la primera cifra y las dos ultimas. `None` si no hay."""
    if valor is None:
        return None
    partes = _FORMATO.match(valor)
    if partes is None:
        return TOTALMENTE_ENMASCARADO
    primera, segunda = partes.groups()
    return f"{primera[0]}***-**{segunda[2:]}"


def cuenta_destino(valor: str) -> str:
    """`6033-9001` -> `6033-****`: el prefijo que el cliente puede reconocer, sin el resto."""
    partes = _FORMATO.match(valor)
    if partes is None:
        return TOTALMENTE_ENMASCARADO
    return f"{partes.group(1)}-****"


def terminada_en(valor: str) -> str:
    """`8712-4455` -> `4455`: las cuatro ultimas cifras, como las muestra un estado de cuenta."""
    partes = _FORMATO.match(valor)
    return partes.group(2) if partes is not None else "****"
