"""Contrato de la forma del error.

Todos los errores de la API tienen la misma forma. Si esto se rompe, el portal tiene
que ramificar por tipo de error para mostrar un mensaje, que es exactamente lo que
este contrato evita.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient

from app.api.errores import CodigoError, ErrorCentinela, RespuestaError
from app.modelos.estados import EstadoAlerta, exigir_transicion


def afirmar_forma_de_error(cuerpo: dict) -> dict:
    """Valida el cuerpo contra el modelo publicado y devuelve el bloque `error`."""
    assert set(cuerpo) == {"error"}, "el cuerpo de error solo tiene la clave 'error'"

    validado = RespuestaError.model_validate(cuerpo)
    bloque = cuerpo["error"]

    assert set(bloque) == {"codigo", "mensaje", "detalles"}
    assert isinstance(bloque["detalles"], list)
    assert bloque["mensaje"]
    assert validado.error.codigo in set(CodigoError)
    return bloque


@pytest.mark.asyncio
async def prueba_una_ruta_que_no_existe_devuelve_la_forma_unica(
    cliente: AsyncClient,
) -> None:
    respuesta = await cliente.get("/api/v1/no-existe")

    assert respuesta.status_code == status.HTTP_404_NOT_FOUND
    error = afirmar_forma_de_error(respuesta.json())
    assert error["codigo"] == CodigoError.NO_ENCONTRADO


@pytest.mark.asyncio
async def prueba_un_parametro_invalido_devuelve_la_forma_unica(
    cliente: AsyncClient,
) -> None:
    """422 de validacion, no el `{"detail": [...]}` por omision de FastAPI."""
    respuesta = await cliente.get("/api/v1/alertas", params={"limite": 0})

    assert respuesta.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    error = afirmar_forma_de_error(respuesta.json())
    assert error["codigo"] == CodigoError.PARAMETROS_INVALIDOS
    assert error["detalles"], "un error de validacion tiene que decir que campo fue"
    assert error["detalles"][0]["campo"] == "limite"


@pytest.mark.asyncio
async def prueba_el_detalle_nombra_el_campo_sin_el_origen(cliente: AsyncClient) -> None:
    """El portal quiere "estado", no ("query", "estado")."""
    respuesta = await cliente.get("/api/v1/alertas", params={"estado": "inventado"})

    error = afirmar_forma_de_error(respuesta.json())
    campos = [detalle["campo"] for detalle in error["detalles"]]
    assert campos == ["estado"]


@pytest.mark.asyncio
async def prueba_un_metodo_no_permitido_devuelve_la_forma_unica(
    cliente: AsyncClient,
) -> None:
    respuesta = await cliente.post("/api/v1/alertas")

    assert respuesta.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    afirmar_forma_de_error(respuesta.json())


@pytest.mark.asyncio
async def prueba_un_error_inesperado_no_filtra_el_detalle_interno() -> None:
    """Un 500 responde JSON con la forma unica, no el HTML de Starlette.

    Se monta una ruta que explota a proposito: es la unica forma de ejercitar el
    manejador de ultimo recurso.
    """
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from app.main import crear_aplicacion

    aplicacion: FastAPI = crear_aplicacion()

    @aplicacion.get("/explota")
    async def explota() -> None:
        raise RuntimeError("secreto de la base de datos que no debe salir")

    async with LifespanManager(aplicacion) as gestionada:
        async with AsyncClient(
            transport=ASGITransport(app=gestionada.app, raise_app_exceptions=False),
            base_url="http://pruebas",
        ) as cliente_http:
            respuesta = await cliente_http.get("/explota")

    assert respuesta.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    error = afirmar_forma_de_error(respuesta.json())
    assert error["codigo"] == CodigoError.ERROR_INTERNO
    assert "secreto" not in respuesta.text


@pytest.mark.asyncio
async def prueba_una_transicion_invalida_sale_como_conflicto() -> None:
    """La maquina de estados, conectada al contrato HTTP.

    Es 409 y no 422: la peticion es valida, lo que no se puede es aplicarla al estado
    actual. Lo usa H-14.
    """
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from app.main import crear_aplicacion

    aplicacion: FastAPI = crear_aplicacion()

    @aplicacion.post("/resolver-de-mentira")
    async def resolver() -> None:
        exigir_transicion(EstadoAlerta.NUEVA, EstadoAlerta.CONFIRMADA)

    async with LifespanManager(aplicacion) as gestionada:
        async with AsyncClient(
            transport=ASGITransport(app=gestionada.app, raise_app_exceptions=False),
            base_url="http://pruebas",
        ) as cliente_http:
            respuesta = await cliente_http.post("/resolver-de-mentira")

    assert respuesta.status_code == status.HTTP_409_CONFLICT
    error = afirmar_forma_de_error(respuesta.json())
    assert error["codigo"] == CodigoError.TRANSICION_INVALIDA
    assert "en_revision" in error["mensaje"], "el mensaje dice que si se puede hacer"


@pytest.mark.asyncio
async def prueba_un_error_de_dominio_respeta_su_estado_y_su_codigo() -> None:
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from app.main import crear_aplicacion

    aplicacion: FastAPI = crear_aplicacion()

    @aplicacion.get("/no-hay")
    async def no_hay() -> None:
        raise ErrorCentinela(
            CodigoError.NO_ENCONTRADO,
            "La alerta ALR-999 no existe",
            status.HTTP_404_NOT_FOUND,
        )

    async with LifespanManager(aplicacion) as gestionada:
        async with AsyncClient(
            transport=ASGITransport(app=gestionada.app, raise_app_exceptions=False),
            base_url="http://pruebas",
        ) as cliente_http:
            respuesta = await cliente_http.get("/no-hay")

    assert respuesta.status_code == status.HTTP_404_NOT_FOUND
    error = afirmar_forma_de_error(respuesta.json())
    assert error["codigo"] == CodigoError.NO_ENCONTRADO
    assert error["mensaje"] == "La alerta ALR-999 no existe"
