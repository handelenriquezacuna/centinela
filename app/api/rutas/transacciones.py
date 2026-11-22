"""H-15 · Busqueda forense sobre el historico de transacciones.

`GET /api/v1/transacciones` con filtros combinables (AND): rango de fecha, rango de
monto en colones enteros, cuenta, canal y estado de la alerta asociada. Pagina acotada
por `app.pagina_maxima`, igual que las alertas. Con `depurar=true` devuelve ademas el
resumen del `explain` de la consulta, para ver que indice uso.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.dependencias import AjustesDep, RepoBusquedaDep
from app.api.errores import (
    CodigoError,
    DetalleError,
    ErrorCentinela,
    respuestas_documentadas,
)
from app.api.esquemas import ElementoTransaccion, PaginaTransacciones
from app.api.paginacion import acotar_limite
from app.modelos import EstadoAlerta
from app.modelos.base import asumir_utc
from app.modelos.transaccional import Canal
from app.repos.busqueda import FiltroBusqueda, problemas_del_filtro

enrutador = APIRouter(tags=["transacciones"])


def exigir_filtro_valido(filtro: FiltroBusqueda) -> None:
    """422 en la forma unica del error si el filtro es incoherente (rango invertido...)."""
    problemas = problemas_del_filtro(filtro)
    if problemas:
        raise ErrorCentinela(
            CodigoError.PARAMETROS_INVALIDOS,
            "La busqueda tiene parametros invalidos",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [
                DetalleError(campo=campo, mensaje=mensaje)
                for campo, mensaje in problemas
            ],
        )


@enrutador.get(
    "/transacciones",
    response_model=PaginaTransacciones,
    response_model_exclude_none=False,
    summary="Busqueda forense de transacciones con filtros combinables",
    responses=respuestas_documentadas(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def buscar_transacciones(
    ajustes: AjustesDep,
    repo: RepoBusquedaDep,
    desde: Annotated[
        datetime | None,
        Query(description="Limite inferior de fecha, inclusive, con zona"),
    ] = None,
    hasta: Annotated[
        datetime | None,
        Query(description="Limite superior de fecha, inclusive, con zona"),
    ] = None,
    monto_min: Annotated[
        int | None,
        Query(ge=0, description="Monto minimo en colones enteros, inclusive"),
    ] = None,
    monto_max: Annotated[
        int | None,
        Query(ge=0, description="Monto maximo en colones enteros, inclusive"),
    ] = None,
    cuenta: Annotated[
        str | None,
        Query(
            max_length=32,
            description="Cuenta de origen (CTA-001 o su numero SINPE) o de destino (SINPE)",
        ),
    ] = None,
    canal: Annotated[
        list[Canal] | None, Query(description="Uno o varios canales ($in)")
    ] = None,
    estado_alerta: Annotated[
        EstadoAlerta | None,
        Query(description="Estado de la alerta asociada a la transaccion"),
    ] = None,
    depurar: Annotated[
        bool, Query(description="Incluye el resumen del explain de la consulta")
    ] = False,
    limite: Annotated[
        int | None,
        Query(ge=1, description="Documentos por pagina; se acota a app.pagina_maxima"),
    ] = None,
    desplazamiento: Annotated[
        int, Query(ge=0, description="Documentos a omitir desde el inicio")
    ] = 0,
) -> PaginaTransacciones:
    """Orden por `fecha` descendente con el identificador como desempate.

    Un monto con decimales o un rango invertido (`desde > hasta`,
    `monto_min > monto_max`) es 422 con la forma unica del error. La alerta asociada se
    resuelve para toda la pagina en una consulta, sin `$lookup` por documento.
    """
    filtro = FiltroBusqueda(
        desde=asumir_utc(desde) if desde is not None else None,
        hasta=asumir_utc(hasta) if hasta is not None else None,
        monto_min=monto_min,
        monto_max=monto_max,
        cuenta=cuenta.strip() if cuenta and cuenta.strip() else None,
        canales=tuple(c.value for c in canal) if canal else (),
        estado_alerta=estado_alerta,
    )
    exigir_filtro_valido(filtro)

    limite_efectivo = acotar_limite(
        limite,
        maximo=ajustes.app.pagina_maxima,
        por_omision=ajustes.app.pagina_por_omision,
    )
    resultado = await repo.buscar(
        filtro, limite=limite_efectivo, desplazamiento=desplazamiento, depurar=depurar
    )
    return PaginaTransacciones(
        total=resultado.total,
        limite=limite_efectivo,
        desplazamiento=desplazamiento,
        elementos=[elemento_de(d) for d in resultado.documentos],
        plan=resultado.plan,
    )


def elemento_de(documento: dict) -> ElementoTransaccion:
    return ElementoTransaccion(
        transaccion_id=documento["_id"],
        cuenta_origen=documento["cuenta_origen"],
        sinpe_origen=documento.get("sinpe_origen"),
        cuenta_destino=documento["cuenta_destino"],
        monto_crc=documento["monto_crc"],
        moneda=documento["moneda"],
        canal=documento["canal"],
        fecha=documento["fecha"],
        alerta_id=documento.get("alerta_id"),
        alerta_estado=documento.get("alerta_estado"),
    )
