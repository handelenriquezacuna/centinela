"""H-19 · Pantalla de busqueda forense y exportacion a CSV.

Reemplaza la maqueta de `/busqueda`. Usa el mismo repositorio y las mismas validaciones
que `GET /api/v1/transacciones` (`FiltroBusqueda`, `problemas_del_filtro`), asi que la
pantalla y la API no pueden divergir.

Un formulario HTML manda los campos en blanco como cadena vacia, y la API los rechazaria
(`monto_min=` no es un entero). Por eso aqui los parametros llegan como texto y se
interpretan con tolerancia: vacio significa "sin filtro", y un valor mal escrito se
muestra como error sobre el formulario en vez de un JSON de 422.

- Las fechas del formulario son dias de Costa Rica: `desde` es el inicio de ese dia y
  `hasta` el final, convertidos a UTC al consultar.
- `/busqueda/exportar.csv` aplica los mismos filtros en el servidor: montos enteros,
  fechas en hora de Costa Rica, hasta `LIMITE_EXPORTACION` filas.

Sin autenticacion hasta H-25.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, Response

from app.api.dependencias import AjustesDep, RepoBusquedaDep
from app.api.errores import CodigoError, DetalleError, ErrorCentinela
from app.api.paginacion import acotar_limite
from app.api.rutas.paginas import ENTORNO, NAVEGACION
from app.api.rutas.transacciones import exigir_filtro_valido
from app.modelos import ZONA_COSTA_RICA, EstadoAlerta
from app.modelos.transaccional import Canal
from app.repos.busqueda import LIMITE_EXPORTACION, FiltroBusqueda, problemas_del_filtro

enrutador = APIRouter(include_in_schema=False)

CAMPOS_FILTRO = (
    "desde",
    "hasta",
    "monto_min",
    "monto_max",
    "cuenta",
    "canal",
    "estado_alerta",
)
ENTERO = re.compile(r"^\d{1,15}$")
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


def _dia(
    valor: str, campo: str, errores: list[tuple[str, str]], *, fin: bool
) -> datetime | None:
    try:
        dia = date.fromisoformat(valor)
    except ValueError:
        errores.append((campo, "debe ser una fecha AAAA-MM-DD"))
        return None
    momento = time(23, 59, 59, 999999) if fin else time(0, 0)
    return datetime.combine(dia, momento, tzinfo=ZONA_COSTA_RICA)


def _entero(valor: str, campo: str, errores: list[tuple[str, str]]) -> int | None:
    if not ENTERO.match(valor):
        errores.append((campo, "debe ser un entero en colones, sin decimales"))
        return None
    return int(valor)


def leer_filtro(crudo: dict[str, str]) -> tuple[FiltroBusqueda, list[tuple[str, str]]]:
    """Del formulario (texto) al filtro, con los errores de lectura y de coherencia."""
    errores: list[tuple[str, str]] = []
    limpio = {campo: crudo.get(campo, "").strip() for campo in CAMPOS_FILTRO}

    canales: tuple[str, ...] = ()
    if limpio["canal"]:
        if limpio["canal"] in {c.value for c in Canal}:
            canales = (limpio["canal"],)
        else:
            errores.append(("canal", "canal desconocido"))
    estado = None
    if limpio["estado_alerta"]:
        try:
            estado = EstadoAlerta(limpio["estado_alerta"])
        except ValueError:
            errores.append(("estado_alerta", "estado desconocido"))

    filtro = FiltroBusqueda(
        desde=_dia(limpio["desde"], "desde", errores, fin=False)
        if limpio["desde"]
        else None,
        hasta=_dia(limpio["hasta"], "hasta", errores, fin=True)
        if limpio["hasta"]
        else None,
        monto_min=_entero(limpio["monto_min"], "monto_min", errores)
        if limpio["monto_min"]
        else None,
        monto_max=_entero(limpio["monto_max"], "monto_max", errores)
        if limpio["monto_max"]
        else None,
        cuenta=limpio["cuenta"] or None,
        canales=canales,
        estado_alerta=estado,
    )
    errores.extend(problemas_del_filtro(filtro))
    return filtro, errores


def _crudo(peticion: Request) -> dict[str, str]:
    return {clave: valor for clave, valor in peticion.query_params.items()}


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


@enrutador.get("/busqueda", response_class=HTMLResponse)
async def busqueda(
    peticion: Request, repo: RepoBusquedaDep, ajustes: AjustesDep
) -> HTMLResponse:
    crudo = _crudo(peticion)
    filtro, errores = leer_filtro(crudo)

    try:
        limite = acotar_limite(
            int(crudo["limite"])
            if ENTERO.match(crudo.get("limite", "")) and int(crudo["limite"]) > 0
            else None,
            maximo=ajustes.app.pagina_maxima,
            por_omision=ajustes.app.pagina_por_omision,
        )
    except ValueError:  # pragma: no cover - ya se descarto lo menor que 1
        limite = ajustes.app.pagina_por_omision
    desplazamiento = (
        int(crudo["desplazamiento"])
        if ENTERO.match(crudo.get("desplazamiento", ""))
        else 0
    )
    depurar = crudo.get("depurar", "") in {"on", "true", "1"}

    resultado = None
    if not errores:
        resultado = await repo.buscar(
            filtro, limite=limite, desplazamiento=desplazamiento, depurar=depurar
        )

    consulta = {c: crudo[c].strip() for c in CAMPOS_FILTRO if crudo.get(c, "").strip()}
    base_pagina = dict(
        consulta, limite=limite, **({"depurar": "on"} if depurar else {})
    )
    anterior = max(desplazamiento - limite, 0)
    siguiente = desplazamiento + limite
    hay_siguiente = resultado is not None and siguiente < resultado.total

    html = ENTORNO.get_template("paginas/busqueda.html").render(
        navegacion=NAVEGACION,
        activa="busqueda",
        valores={c: crudo.get(c, "") for c in CAMPOS_FILTRO},
        limite=limite,
        depurar=depurar,
        canales=[c.value for c in Canal],
        estados=[e.value for e in EstadoAlerta],
        errores=errores,
        resultado=resultado,
        desplazamiento=desplazamiento,
        primero=desplazamiento + 1,
        ultimo=desplazamiento + (len(resultado.documentos) if resultado else 0),
        enlace_anterior=(
            "/busqueda?" + urlencode(dict(base_pagina, desplazamiento=anterior))
        )
        if desplazamiento > 0
        else None,
        enlace_siguiente=(
            "/busqueda?" + urlencode(dict(base_pagina, desplazamiento=siguiente))
        )
        if hay_siguiente
        else None,
        enlace_csv="/busqueda/exportar.csv?" + urlencode(consulta),
        limite_exportacion=LIMITE_EXPORTACION,
    )
    return HTMLResponse(html, status_code=422 if errores else 200)


@enrutador.get("/busqueda/exportar.csv")
async def exportar_csv(peticion: Request, repo: RepoBusquedaDep) -> Response:
    """Los mismos filtros que la pantalla, en el servidor. Filtro invalido: 422 del contrato."""
    filtro, errores = leer_filtro(_crudo(peticion))
    if errores:
        raise ErrorCentinela(
            CodigoError.PARAMETROS_INVALIDOS,
            "La busqueda tiene parametros invalidos",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [DetalleError(campo=campo, mensaje=mensaje) for campo, mensaje in errores],
        )
    exigir_filtro_valido(filtro)

    resultado = await repo.buscar(filtro, limite=LIMITE_EXPORTACION)
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
