"""Acotado del limite de pagina contra `app.pagina_maxima`."""

from __future__ import annotations

import pytest

from app.api.paginacion import acotar_limite


def prueba_sin_limite_usa_el_de_omision() -> None:
    assert acotar_limite(None, maximo=200, por_omision=50) == 50


def prueba_un_limite_valido_se_respeta() -> None:
    assert acotar_limite(10, maximo=200, por_omision=50) == 10


def prueba_un_limite_excesivo_se_acota_al_maximo() -> None:
    """El caso que justifica la funcion: nadie arma 100.000 documentos en memoria."""
    assert acotar_limite(100_000, maximo=200, por_omision=50) == 200


def prueba_el_limite_igual_al_maximo_pasa() -> None:
    assert acotar_limite(200, maximo=200, por_omision=50) == 200


def prueba_el_limite_uno_pasa() -> None:
    assert acotar_limite(1, maximo=200, por_omision=50) == 1


@pytest.mark.parametrize("invalido", [0, -1, -999])
def prueba_un_limite_menor_que_uno_es_error(invalido: int) -> None:
    """No se acota a 1: pedir cero documentos es una peticion sin sentido."""
    with pytest.raises(ValueError, match="mayor o igual a 1"):
        acotar_limite(invalido, maximo=200, por_omision=50)


def prueba_el_de_omision_tambien_queda_acotado() -> None:
    """Si alguien configura pagina_por_omision mayor que pagina_maxima.

    La configuracion ya lo valida al arrancar, pero la funcion no depende de eso:
    acotar es su trabajo, no confiar.
    """
    assert acotar_limite(None, maximo=5, por_omision=50) == 5
