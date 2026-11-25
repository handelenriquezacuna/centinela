"""Asignador de identificadores de transaccion para todo lo que entra DESPUES de la siembra.

Hay tres familias de ids en la base: la demo sembrada (`TXN-001`..`TXN-007`, tres digitos),
y todo lo posterior, que usa seis digitos (`TXN-NNNNNN`): el generador y la cadena del demo
en vivo. Una sola regla para lo posterior: "el siguiente al mayor TXN de seis digitos".

Por que importa: el rezago de D1 (`filtro_pendientes`) cuenta pendientes con `_id` mayor al
de la ultima transaccion que D1 termino, comparando TEXTO. Con un solo asignador, lo que
entra despues siempre es mayor, en orden de insercion, y la comparacion de texto coincide
con la numerica porque el ancho es fijo. Quien inserte con otro esquema (por ejemplo un
rango propio como TXN-9NNNNN) deja el rezago en 0 con miles de pendientes.

`tareas.py cargar` no pasa por aqui: sus ids vienen del archivo.
"""

from __future__ import annotations

import re
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.modelos import COLECCION_TRANSACCIONES

ANCHO_ID = 6
PREFIJO = "TXN-"
PRIMER_NUMERO = 1
ULTIMO_NUMERO = 10**ANCHO_ID - 1
ID_MINIMO = f"{PREFIJO}{0:0{ANCHO_ID}d}"  # TXN-000000: cota inferior exclusiva
ID_MAXIMO = f"{PREFIJO}{ULTIMO_NUMERO:0{ANCHO_ID}d}"  # TXN-999999

# Ancla en ambos extremos: `TXN-007` y `TXN-0000007` no son ids de seis digitos.
PATRON_ID_DE_SEIS = rf"^{PREFIJO}[0-9]{{{ANCHO_ID}}}$"
_ID_DE_SEIS = re.compile(rf"^{PREFIJO}([0-9]{{{ANCHO_ID}}})$")
_FILTRO_DE_SEIS = {"_id": {"$regex": PATRON_ID_DE_SEIS}}


def id_transaccion(numero: int) -> str:
    """`42` -> `TXN-000042`."""
    if not PRIMER_NUMERO <= numero <= ULTIMO_NUMERO:
        raise ValueError(f"el numero {numero} no cabe en un id de {ANCHO_ID} digitos")
    return f"{PREFIJO}{numero:0{ANCHO_ID}d}"


def numero_de(identificador: str | None) -> int | None:
    """Numero de un id de seis digitos; `None` si no lo es (TXN-007, TXN-0000001, otro texto)."""
    if identificador is None:
        return None
    coincidencia = _ID_DE_SEIS.match(identificador)
    return int(coincidencia.group(1)) if coincidencia else None


def es_id_de_seis_digitos(identificador: str | None) -> bool:
    return numero_de(identificador) is not None


def numero_siguiente(mayor_id: str | None) -> int:
    """El numero que sigue al mayor id de seis digitos; 1 si no hay ninguno. Funcion pura.

    Un `mayor_id` que no sea de seis digitos se trata como "ninguno" (la demo de tres
    digitos no cuenta). Cuando se acaba el rango (TXN-999999) se detiene y lo dice: seguir
    con siete digitos rompe la comparacion de texto del rezago.
    """
    numero = numero_de(mayor_id)
    if numero is None:
        return PRIMER_NUMERO
    if numero >= ULTIMO_NUMERO:
        raise OverflowError(
            f"el ultimo id de transaccion es {mayor_id}: no quedan identificadores de "
            f"{ANCHO_ID} digitos"
        )
    return numero + 1


async def mayor_id_de_seis_digitos(base: AsyncDatabase[Any]) -> str | None:
    """El mayor `TXN-NNNNNN` que existe, o `None`. Una lectura por el indice de `_id`."""
    ultima = await base[COLECCION_TRANSACCIONES].find_one(
        _FILTRO_DE_SEIS, {"_id": 1}, sort=[("_id", -1)]
    )
    return ultima["_id"] if ultima is not None else None


async def siguiente_numero_transaccion(base: AsyncDatabase[Any]) -> int:
    """Primer numero libre para insertar: el siguiente al mayor TXN de seis digitos."""
    return numero_siguiente(await mayor_id_de_seis_digitos(base))
