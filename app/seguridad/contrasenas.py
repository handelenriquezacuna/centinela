"""Contrasenas cifradas con scrypt de la biblioteca estandar.

Se guarda un hash con sal aleatoria por usuario y los parametros dentro de la propia
credencial: subir el costo mas adelante no invalida las contrasenas ya guardadas,
porque cada una se verifica con los parametros con que se creo.

    {"hash": <base64>, "sal": <base64>,
     "parametros": {"algoritmo": "scrypt", "n": 16384, "r": 8, "p": 1, "dklen": 32}}

La comparacion es en tiempo constante (`hmac.compare_digest`). Si el agente no existe o
no tiene credencial, `verificar` igual calcula un hash contra una credencial de relleno:
sin eso, responder mas rapido delataria cuales identificadores existen.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any

ALGORITMO = "scrypt"
LARGO_MINIMO = 12

# Costo por omision (OWASP: scrypt n=2^14, r=8, p=1 es el minimo aceptable). n=2^14 con
# r=8 usa 16 MiB y tarda decenas de milisegundos: poco para un login legitimo, caro para
# quien prueba claves a ritmo de maquina.
PARAMETROS_POR_OMISION: dict[str, Any] = {
    "algoritmo": ALGORITMO,
    "n": 2**14,
    "r": 8,
    "p": 1,
    "dklen": 32,
}

# Tope de lo que se acepta leer de la base: una credencial con n gigante no debe poder
# agotar la memoria del proceso al verificarla.
_N_MAXIMO = 2**20
_LARGO_SAL = 16


def _b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode("ascii")


def _derivar(contrasena: str, sal: bytes, parametros: dict[str, Any]) -> bytes:
    n, r, p, dklen = (int(parametros[k]) for k in ("n", "r", "p", "dklen"))
    if parametros.get("algoritmo") != ALGORITMO or n < 2 or n > _N_MAXIMO or n & (n - 1):
        raise ValueError("parametros de credencial no soportados")
    return hashlib.scrypt(
        contrasena.encode("utf-8"),
        salt=sal,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=128 * n * r * 2 + 1024 * 1024,
    )


def cifrar(contrasena: str, parametros: dict[str, Any] | None = None) -> dict[str, Any]:
    """Credencial lista para guardar en `agentes.credencial`."""
    usados = dict(parametros or PARAMETROS_POR_OMISION)
    sal = secrets.token_bytes(_LARGO_SAL)
    return {
        "hash": _b64(_derivar(contrasena, sal, usados)),
        "sal": _b64(sal),
        "parametros": usados,
    }


# Credencial de relleno para igualar el tiempo cuando no hay nada contra que comparar.
_RELLENO = cifrar("relleno-sin-uso-no-es-una-contrasena")


def verificar(contrasena: str, credencial: dict[str, Any] | None) -> bool:
    """True si la contrasena corresponde a la credencial. Nunca lanza por datos malos."""
    esperada = credencial if credencial else _RELLENO
    try:
        sal = base64.b64decode(esperada["sal"])
        guardado = base64.b64decode(esperada["hash"])
        calculado = _derivar(contrasena, sal, esperada["parametros"])
    except (KeyError, TypeError, ValueError):
        return False
    coincide = hmac.compare_digest(calculado, guardado)
    return coincide and credencial is not None


def problemas_de_contrasena(contrasena: str) -> list[str]:
    """Reglas minimas al CREAR una contrasena (no al verificarla)."""
    if len(contrasena) < LARGO_MINIMO:
        return [f"debe tener al menos {LARGO_MINIMO} caracteres"]
    return []
