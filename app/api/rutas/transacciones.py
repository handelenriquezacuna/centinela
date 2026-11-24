"""H-15 · Busqueda forense sobre el historico de transacciones.

`GET /api/v1/transacciones` con filtros combinables (AND): rango de fecha, rango de
monto en colones enteros, cuenta, canal y estado de la alerta asociada. Pagina acotada
por `app.pagina_maxima`, igual que las alertas. Con `depurar=true` devuelve ademas el
resumen del `explain` de la consulta, para ver que indice uso.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from fastapi.responses import Response

from app.api.dependencias import AjustesDep, RepoBusquedaDep
from app.api.errores import (
    CodigoError,
    DetalleError,
    ErrorCentinela,
    respuestas_documentadas,
)
from app.api.esquemas import ElementoTransaccion, PaginaTransacciones
from app.api.paginacion import acotar_limite
from app.modelos import ZONA_COSTA_RICA, EstadoAlerta
from app.modelos.base import asumir_utc
from app.modelos.transaccional import Canal
from app.repos.busqueda import LIMITE_EXPORTACION, FiltroBusqueda, problemas_del_filtro

enrutador = APIRouter(tags=["transacciones"])

COLUMNAS_CSV = (
    "transaccion_id",
    "fecha_hora_cr",
    "cuenta_origen",
    "sinpe_origen",
    "cuenta_destino",
    "monto_crc",
    "moneda",
    "canal",
    "alerta_id",
    "alerta_estado",
)


def fila_csv(documento: dict[str, Any]) -> list[Any]:
    return [
        documento["_id"],
        documento["fecha"].astimezone(ZONA_COSTA_RICA).strftime("%Y-%m-%d %H:%M:%S"),
        documento["cuenta_origen"],
        documento.get("sinpe_origen") or "",
        documento["cuenta_destino"],
        int(documento["monto_crc"]),
        documento["moneda"],
        documento["canal"],
        documento.get("alerta_id") or "",
        documento.get("alerta_estado") or "",
    ]


def respuesta_csv(resultado: Any) -> Response:
    """El CSV de una busqueda: montos enteros, fechas en hora de Costa Rica."""
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\r\n")
    escritor.writerow(COLUMNAS_CSV)
    for documento in resultado.documentos:
        escritor.writerow(fila_csv(documento))
    return Response(
        salida.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="transacciones.csv"',
            "X-Total-Coincidencias": str(resultado.total),
            "X-Filas-Exportadas": str(len(resultado.documentos)),
        },
    )


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


@enrutador.get(
    "/transacciones/exportar.csv",
    response_class=Response,
    summary="Exporta a CSV las transacciones de una busqueda forense",
    responses={
        status.HTTP_200_OK: {"content": {"text/csv": {}}, "description": "CSV con encabezado"},
        **respuestas_documentadas(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ),
    },
)
async def exportar_transacciones_csv(
    repo: RepoBusquedaDep,
    desde: Annotated[datetime | None, Query(description="Limite inferior de fecha, con zona")] = None,
    hasta: Annotated[datetime | None, Query(description="Limite superior de fecha, con zona")] = None,
    monto_min: Annotated[int | None, Query(ge=0, description="Colones enteros")] = None,
    monto_max: Annotated[int | None, Query(ge=0, description="Colones enteros")] = None,
    cuenta: Annotated[str | None, Query(max_length=32)] = None,
    canal: Annotated[list[Canal] | None, Query()] = None,
    estado_alerta: Annotated[EstadoAlerta | None, Query()] = None,
) -> Response:
    """Los mismos filtros que `GET /api/v1/transacciones`, hasta `LIMITE_EXPORTACION` filas.

    Montos enteros y fechas en hora de Costa Rica. El total de coincidencias viaja en
    `X-Total-Coincidencias` y las filas escritas en `X-Filas-Exportadas`.
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
    return respuesta_csv(await repo.buscar(filtro, limite=LIMITE_EXPORTACION))


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
