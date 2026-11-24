"""Tablero (H-20) y grafo de la red de mulas (T-35). Paginas HTML, fuera de `/api/v1`.

- `/tablero`: lee el rollup `indicadores_diarios` (lo materializa H-22) y lo dibuja con SVG
  generado aqui, en el servidor. No recalcula nada en la peticion y muestra la marca de
  tiempo del ultimo calculo.
- `/red-mulas` y `/red-mulas/{cuenta}`: nodos y aristas de `RepoRedMulas` (el mismo que
  sirve `GET /api/v1/red-mulas/{cuenta}`) como un grafo SVG, con el monto de cada
  transferencia y los minutos entre eslabones, y las propuestas para `listas_riesgo`.
  Supervisor y administrador ven ademas un boton por propuesta (la agrega a la lista,
  T-51) y las entradas ya vigentes de la red con "Dar de baja" (D-41).
- El Sankey del recorrido (T-55) sale de `app/api/sankey.py`. `GET /red-mulas/{cuenta}/sankey`
  es el fragmento que usa el detalle de la alerta; `POST /red-mulas/{cuenta}/mulas/{objetivo}`
  marca una cuenta del recorrido como mula (motivo obligatorio) y devuelve el Sankey de nuevo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.api.dependencias import RepoDetalleDep, RepoRedMulasPortalDep, RepoTableroDep
from app.api.errores import ErrorCentinela
from app.api.graficos import (
    barras_horizontales,
    columnas_por_hora,
    disponer_grafo,
    graficos_tablero,
    tiempo_corto,
)
from app.api.rutas.inteligencia import CuentaRuta
from app.api.rutas.listas_riesgo import (
    MOTIVO_MAX,
    MOTIVO_MIN,
    CuentaSinpeRuta,
    MarcaMula,
    RepoListasRiesgoDep,
    RiesgoIdRuta,
    aprobar_propuesta,
    dar_de_baja,
    marcar_mula,
)
from app.api.rutas.paginas import ENTORNO, NAVEGACION
from app.api.sankey import Victima, disponer_sankey, victima_de
from app.api.sesion import SesionDep, permite_ruta
from app.modelos.base import asumir_utc
from app.repos.red_mulas import (
    SALTOS_MAXIMOS,
    VENTANA_MINUTOS,
    CuentaNoEncontrada,
    RedMulas,
    RepoRedMulas,
)
from app.repos.tablero import DIAS_MAXIMOS

enrutador = APIRouter(include_in_schema=False)


ENTORNO.filters["tiempo_corto"] = tiempo_corto

# Se reexportan: las pruebas y la API las importan desde aqui o desde app/api/graficos.py.
__all__ = ["barras_horizontales", "columnas_por_hora", "disponer_grafo", "tiempo_corto"]

PERIODOS = (1, 7, 14, 30)
COMANDO_INDICADORES = "python tareas.py indicadores"


def _pagina(plantilla: str, activa: str, estado: int = 200, **contexto: Any) -> HTMLResponse:
    html = ENTORNO.get_template(plantilla).render(
        navegacion=NAVEGACION, activa=activa, **contexto
    )
    return HTMLResponse(html, status_code=estado)


@enrutador.get("/tablero", response_class=HTMLResponse)
async def tablero(
    repo: RepoTableroDep,
    dias: Annotated[int, Query(ge=1, le=DIAS_MAXIMOS, description="Largo del periodo")] = 7,
) -> HTMLResponse:
    resumen = await repo.leer(dias)
    return _pagina(
        "paginas/tablero.html",
        "tablero",
        resumen=resumen,
        graficos=None if resumen["vacio"] else graficos_tablero(resumen),
        periodos=PERIODOS,
        dias=dias,
        comando=COMANDO_INDICADORES,
    )


# ---------------------------------------------------------------------------
# Red de mulas
# ---------------------------------------------------------------------------

@enrutador.get("/red-mulas")
async def red_mulas_formulario(
    cuenta: Annotated[str | None, Query(max_length=20)] = None,
) -> Any:
    """El formulario de la barra de navegacion: pide una cuenta y redirige a su grafo."""
    destino = (cuenta or "").strip()
    if destino:
        return RedirectResponse(f"/red-mulas/{destino}")
    return _pagina("paginas/red_mulas.html", "red_mulas", red=None, grafo=None, cuenta=None)


def _puede_gestionar(cuenta: str) -> bool:
    return permite_ruta(f"/red-mulas/{cuenta}/propuestas/0000-0000", "POST")


@enrutador.get("/red-mulas/{cuenta}", response_class=HTMLResponse)
async def red_mulas_grafo(
    cuenta: CuentaRuta,
    repo: RepoRedMulasPortalDep,
    listas: RepoListasRiesgoDep,
    desde: Annotated[datetime | None, Query(description="Instante de partida (UTC)")] = None,
    ventana_minutos: Annotated[int, Query(ge=1, le=1440)] = VENTANA_MINUTOS,
    saltos: Annotated[int, Query(ge=1, le=SALTOS_MAXIMOS)] = SALTOS_MAXIMOS,
) -> HTMLResponse:
    try:
        red = await repo.recorrer(
            cuenta,
            desde=asumir_utc(desde) if desde is not None else None,
            ventana_minutos=ventana_minutos,
            saltos=saltos,
        )
    except CuentaNoEncontrada as fallo:
        return _pagina(
            "paginas/red_mulas.html",
            "red_mulas",
            estado=404,
            red=None,
            grafo=None,
            cuenta=cuenta,
            error=str(fallo),
        )
    puede = _puede_gestionar(cuenta)
    entradas: list[dict[str, Any]] = []
    en_lista = [nodo.cuenta for nodo in red.nodos if nodo.en_lista_riesgo]
    if puede and en_lista:  # solo quien puede dar de baja necesita verlas
        por_cuenta = await listas.entradas_de_cuentas(en_lista)
        entradas = [e for sinpe in en_lista for e in por_cuenta.get(sinpe, [])]
    return _pagina(
        "paginas/red_mulas.html",
        "red_mulas",
        red=red,
        grafo=disponer_grafo(red) if red.aristas else None,
        error=None,
        entradas_lista=entradas,
        **_contexto_sankey(red, cuenta),
    )


def _contexto_sankey(
    red: RedMulas,
    cuenta: str,
    victima: Victima | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Lo que necesita `paginas/sankey.html`. `consulta` repite los parametros de la red
    en los formularios, para que al marcar se vuelva a dibujar la misma red."""
    consulta = {"ventana_minutos": red.ventana_minutos, "saltos": red.saltos_maximos}
    if red.desde is not None:
        consulta["desde"] = red.desde.isoformat()
    if victima is not None:
        consulta["transaccion"] = victima.transaccion_id
    return {
        "sankey": disponer_sankey(red, victima),
        "consulta": urlencode(consulta),
        "puede_gestionar": _puede_gestionar(cuenta),
        "cuenta": cuenta,
        "mensaje": None,
        "es_error": False,
        "cuenta_mensaje": None,
        "motivo_previo": "",
        **extra,
    }


async def _red(
    repo: RepoRedMulas,
    cuenta: str,
    desde: datetime | None,
    ventana_minutos: int,
    saltos: int,
) -> RedMulas:
    return await repo.recorrer(
        cuenta,
        desde=asumir_utc(desde) if desde is not None else None,
        ventana_minutos=ventana_minutos,
        saltos=saltos,
    )


def _sin_recorrido(mensaje: str, estado: int) -> HTMLResponse:
    return HTMLResponse(
        f'<div id="sankey"><p class="nota-tecnica" id="sankey-vacio">{mensaje}</p></div>',
        status_code=estado,
    )


@enrutador.get("/red-mulas/{cuenta}/sankey", response_class=HTMLResponse)
async def red_mulas_sankey(
    cuenta: CuentaRuta,
    repo: RepoRedMulasPortalDep,
    repo_detalle: RepoDetalleDep,
    desde: Annotated[datetime | None, Query(description="Instante de partida (UTC)")] = None,
    ventana_minutos: Annotated[int, Query(ge=1, le=1440)] = VENTANA_MINUTOS,
    saltos: Annotated[int, Query(ge=1, le=SALTOS_MAXIMOS)] = SALTOS_MAXIMOS,
    transaccion: Annotated[
        str | None, Query(description="Transaccion de partida: su cliente es la victima")
    ] = None,
) -> HTMLResponse:
    """El Sankey solo, como fragmento: el detalle de la alerta lo pide al cargar."""
    try:
        red = await _red(repo, cuenta, desde, ventana_minutos, saltos)
    except CuentaNoEncontrada as fallo:
        return _sin_recorrido(str(fallo), 404)
    if not red.aristas:
        return _sin_recorrido("Esta cuenta no tiene salidas registradas en la ventana.", 200)
    victima = await victima_de(repo_detalle, transaccion)
    return _fragmento("paginas/sankey.html", **_contexto_sankey(red, cuenta, victima))


def _fragmento(plantilla: str, estado: int = 200, **contexto: Any) -> HTMLResponse:
    return HTMLResponse(ENTORNO.get_template(plantilla).render(**contexto), status_code=estado)


@enrutador.post("/red-mulas/{cuenta}/propuestas/{propuesta}", response_class=HTMLResponse)
async def aprobar_desde_red(
    cuenta: CuentaRuta,
    propuesta: CuentaSinpeRuta,
    repo: RepoListasRiesgoDep,
    sesion: SesionDep,
) -> HTMLResponse:
    """Aprueba una propuesta con la misma funcion que la API y devuelve su fila."""
    resultado = await aprobar_propuesta(repo, cuenta, propuesta, sesion)
    mensaje = (
        f"Agregada como {resultado.entrada.riesgo_id}"
        if resultado.creada
        else f"Ya estaba vigente como {resultado.entrada.riesgo_id}: no se duplico"
    )
    return _fragmento(
        "paginas/propuesta_item.html", cuenta=cuenta, sinpe=propuesta, mensaje=mensaje, es_error=False
    )


@enrutador.post("/red-mulas/{cuenta}/mulas/{objetivo}", response_class=HTMLResponse)
async def marcar_desde_sankey(
    cuenta: CuentaRuta,
    objetivo: CuentaSinpeRuta,
    peticion: Request,
    repo_red: RepoRedMulasPortalDep,
    repo_detalle: RepoDetalleDep,
    repo: RepoListasRiesgoDep,
    sesion: SesionDep,
    desde: Annotated[datetime | None, Query(description="Instante de partida (UTC)")] = None,
    ventana_minutos: Annotated[int, Query(ge=1, le=1440)] = VENTANA_MINUTOS,
    saltos: Annotated[int, Query(ge=1, le=SALTOS_MAXIMOS)] = SALTOS_MAXIMOS,
    transaccion: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    """Marca `objetivo` como mula con la misma funcion que la API y dibuja el Sankey otra vez.

    Sin motivo (o fuera de 5 a 200 caracteres) responde 422 con el Sankey y el aviso, sin
    escribir. Quien marca sale de la sesion.
    """
    cuerpo = parse_qs((await peticion.body()).decode("utf-8"), keep_blank_values=True)
    motivo = cuerpo.get("motivo", [""])[0]
    mensaje: str
    estado = 200
    es_error = False
    try:
        marca = MarcaMula(cuenta=objetivo, motivo=motivo)
        resultado = await marcar_mula(repo, marca, sesion)
        mensaje = (
            f"La cuenta {objetivo} quedó en lista de riesgo ({resultado.entrada.riesgo_id})."
            if resultado.creada
            else f"La cuenta {objetivo} ya estaba en lista de riesgo ({resultado.entrada.riesgo_id})."
        )
    except ValidationError:
        estado, es_error = 422, True
        mensaje = (
            f"El motivo es obligatorio: entre {MOTIVO_MIN} y {MOTIVO_MAX} caracteres. "
            f"No se marcó la cuenta {objetivo}."
        )
    try:
        red = await _red(repo_red, cuenta, desde, ventana_minutos, saltos)
    except CuentaNoEncontrada as fallo:
        return _sin_recorrido(str(fallo), 404)
    if not red.aristas:
        return _sin_recorrido("Esta cuenta no tiene salidas registradas en la ventana.", estado)
    return _fragmento(
        "paginas/sankey.html",
        estado=estado,
        **_contexto_sankey(
            red,
            cuenta,
            await victima_de(repo_detalle, transaccion),
            mensaje=mensaje,
            es_error=es_error,
            cuenta_mensaje=objetivo if es_error else None,
            motivo_previo=motivo.strip() if es_error else "",
        ),
    )


@enrutador.post("/red-mulas/{cuenta}/listas-riesgo/{riesgo_id}/baja", response_class=HTMLResponse)
async def baja_desde_red(
    cuenta: CuentaRuta,
    riesgo_id: RiesgoIdRuta,
    repo: RepoListasRiesgoDep,
    sesion: SesionDep,
) -> HTMLResponse:
    """Da de baja con la misma funcion que la API y devuelve la fila con el resultado."""
    try:
        entrada = await dar_de_baja(repo, riesgo_id, sesion)
    except ErrorCentinela as fallo:
        return HTMLResponse(
            f'<li><span class="mensaje mensaje-error" role="alert">{fallo.mensaje}</span></li>',
            status_code=fallo.estado_http,
        )
    return _fragmento(
        "paginas/entrada_lista.html",
        cuenta=cuenta,
        entrada=entrada,
        mensaje=f"Dada de baja: {riesgo_id} ya no esta en la lista",
        es_error=False,
    )
