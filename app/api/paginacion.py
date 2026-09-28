"""Acotado de la paginacion.

Funcion aparte y con pruebas propias porque es la unica logica de la capa HTTP en
H-00A, y porque el error que evita es concreto: una peticion con `limite=100000`
que hace que el servidor arme cien mil documentos en memoria.
"""

from __future__ import annotations


def acotar_limite(limite: int | None, maximo: int, por_omision: int) -> int:
    """Limite efectivo de una consulta.

    - Sin `limite`, se usa `app.pagina_por_omision`.
    - Con `limite`, nunca se pasa de `app.pagina_maxima`: se acota en silencio en vez
      de rechazar la peticion, porque pedir mas de lo permitido no es un error del
      cliente, es una expectativa que el servidor ajusta. La respuesta lleva el
      `limite` que de verdad se aplico, asi que el cliente se da cuenta.
    - `limite` menor que 1 no se acota: es una peticion sin sentido y se rechaza
      antes, en la validacion de la ruta (por eso la ruta declara `ge=1`).
    """
    if limite is None:
        return min(por_omision, maximo)
    if limite < 1:
        raise ValueError(f"limite debe ser mayor o igual a 1, llego {limite}")
    return min(limite, maximo)
