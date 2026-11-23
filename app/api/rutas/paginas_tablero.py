"""Tablero (H-20) y grafo de la red de mulas (T-35). Paginas HTML, fuera de `/api/v1`.

- `/tablero`: lee el rollup `indicadores_diarios` (lo materializa H-22) y lo dibuja con SVG
  generado aqui, en el servidor. No recalcula nada en la peticion y muestra la marca de
  tiempo del ultimo calculo.
- `/red-mulas` y `/red-mulas/{cuenta}`: nodos y aristas de `RepoRedMulas` (el mismo que
  sirve `GET /api/v1/red-mulas/{cuenta}`) como un grafo SVG, con el monto de cada
  transferencia y los minutos entre eslabones, y las propuestas para `listas_riesgo`.
  Supervisor y administrador ven ademas un boton por propuesta (la agrega a la lista,
  T-51) y las entradas ya vigentes de la red con "Dar de baja" (D-41).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencias import RepoRedMulasPortalDep, RepoTableroDep
from app.api.errores import ErrorCentinela
from app.api.rutas.inteligencia import CuentaRuta
from app.api.rutas.listas_riesgo import (
    CuentaSinpeRuta,
    RepoListasRiesgoDep,
    RiesgoIdRuta,
    aprobar_propuesta,
    dar_de_baja,
)
from app.api.rutas.paginas import ENTORNO, NAVEGACION
from app.api.sesion import SesionDep, permite_ruta
from app.modelos.base import asumir_utc
from app.repos.red_mulas import (
    SALTOS_MAXIMOS,
    VENTANA_MINUTOS,
    CuentaNoEncontrada,
    RedMulas,
)
from app.repos.tablero import DIAS_MAXIMOS

enrutador = APIRouter(include_in_schema=False)


def tiempo_corto(segundos: float | None) -> str:
    """90 -> '1 min 30 s'; 3700 -> '1 h 1 min'. Para las cifras del tablero."""
    if segundos is None:
        return "-"
    total = round(segundos)
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas} h {minutos} min"
    if minutos:
        return f"{minutos} min {seg} s"
    return f"{seg} s"


ENTORNO.filters["tiempo_corto"] = tiempo_corto

PERIODOS = (1, 7, 14, 30)
COMANDO_INDICADORES = "python tareas.py indicadores"


def _pagina(plantilla: str, activa: str, estado: int = 200, **contexto: Any) -> HTMLResponse:
    html = ENTORNO.get_template(plantilla).render(
        navegacion=NAVEGACION, activa=activa, **contexto
    )
    return HTMLResponse(html, status_code=estado)


# ---------------------------------------------------------------------------
# Geometria de los graficos: el servidor calcula, la plantilla solo dibuja.
# ---------------------------------------------------------------------------

ETIQUETA, ANCHO_MAX, ALTO_FILA = 120, 260, 26


def barras_horizontales(filas: list[tuple[str, int, str]]) -> dict[str, Any]:
    """`filas` = (rotulo, valor, clase). Devuelve la geometria de un grafico de barras."""
    maximo = max((valor for _, valor, _ in filas), default=0)
    return {
        "ancho": ETIQUETA + ANCHO_MAX + 50,
        "alto": max(1, len(filas)) * ALTO_FILA,
        "etiqueta": ETIQUETA,
        "filas": [
            {
                "y": i * ALTO_FILA,
                "rotulo": rotulo,
                "valor": valor,
                "clase": clase,
                # Una barra con valor > 0 nunca desaparece por ser chica.
                "ancho": max(2, round(valor * ANCHO_MAX / maximo)) if valor > 0 else 0,
            }
            for i, (rotulo, valor, clase) in enumerate(filas)
        ],
    }


def columnas_por_hora(por_hora: dict[str, int]) -> dict[str, Any]:
    alto_max, paso = 120, 20
    maximo = max(por_hora.values(), default=0)
    columnas = []
    for hora, valor in sorted(por_hora.items()):
        alto = max(2, round(valor * alto_max / maximo)) if valor > 0 else 0
        columnas.append(
            {"x": int(hora) * paso, "y": alto_max - alto, "alto": alto, "hora": hora, "valor": valor}
        )
    return {"ancho": 24 * paso, "alto": alto_max + 20, "base": alto_max, "paso": paso, "columnas": columnas}


def _graficos(resumen: dict[str, Any]) -> dict[str, Any]:
    actual = resumen["actual"]
    graficos: dict[str, Any] = {
        "estado": barras_horizontales(
            [(k, v, f"barra-{k}") for k, v in actual["por_estado"].items()]
        ),
        "severidad": barras_horizontales(
            [(k, v, f"barra-{k.lower()}") for k, v in actual["por_severidad"].items()]
        ),
        "agente": None,
        "hora": None,
    }
    if actual["por_agente"] is not None:
        orden = sorted(actual["por_agente"].items(), key=lambda par: (-par[1], par[0]))
        graficos["agente"] = barras_horizontales([(k, v, "barra-agente") for k, v in orden])
    if actual["por_hora"] is not None:
        graficos["hora"] = columnas_por_hora(actual["por_hora"])
    return graficos


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
        graficos=None if resumen["vacio"] else _graficos(resumen),
        periodos=PERIODOS,
        dias=dias,
        comando=COMANDO_INDICADORES,
    )


# ---------------------------------------------------------------------------
# Red de mulas
# ---------------------------------------------------------------------------

COLUMNA, FILA, NODO_ANCHO, NODO_ALTO, MARGEN = 230, 84, 140, 44, 20


def disponer_grafo(red: RedMulas) -> dict[str, Any]:
    """Posiciones del grafo: una columna por nivel, los nodos de un nivel apilados."""
    por_nivel: dict[int, list[Any]] = {}
    for nodo in red.nodos:
        por_nivel.setdefault(nodo.nivel, []).append(nodo)
    filas_max = max((len(v) for v in por_nivel.values()), default=1)
    posiciones: dict[str, tuple[int, int]] = {}
    nodos = []
    for nivel, grupo in sorted(por_nivel.items()):
        desplazamiento = (filas_max - len(grupo)) * FILA // 2
        for i, nodo in enumerate(grupo):
            x = MARGEN + nivel * COLUMNA
            y = MARGEN + desplazamiento + i * FILA
            posiciones[nodo.cuenta] = (x, y)
            nodos.append(
                {
                    "x": x,
                    "y": y,
                    "ancho": NODO_ANCHO,
                    "alto": NODO_ALTO,
                    "cuenta": nodo.cuenta,
                    "cuenta_id": nodo.cuenta_id,
                    "externa": nodo.externa,
                    "en_lista": nodo.en_lista_riesgo,
                    "inicio": nodo.nivel == 0,
                }
            )
    aristas = []
    for arista in red.aristas:
        ox, oy = posiciones[arista.origen]
        dx, dy = posiciones[arista.destino]
        if dx > ox:  # hacia adelante: del borde derecho al izquierdo
            x1, y1, x2, y2 = ox + NODO_ANCHO, oy + NODO_ALTO // 2, dx, dy + NODO_ALTO // 2
        else:  # un retorno (cuenta que ya aparecio): de centro a centro, por abajo
            x1, y1 = ox + NODO_ANCHO // 2, oy + NODO_ALTO
            x2, y2 = dx + NODO_ANCHO // 2, dy + (NODO_ALTO if dy >= oy else 0)
        aristas.append(
            {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "tx": (x1 + x2) // 2,
                "ty": (y1 + y2) // 2 - 6,
                "monto_crc": arista.monto_crc,
                "minutos": arista.minutos_desde_anterior,
                "salto": arista.salto,
            }
        )
    niveles = max(por_nivel, default=0)
    return {
        "ancho": MARGEN * 2 + niveles * COLUMNA + NODO_ANCHO,
        "alto": MARGEN * 2 + (filas_max - 1) * FILA + NODO_ALTO,
        "nodos": nodos,
        "aristas": aristas,
    }


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
        cuenta=cuenta,
        error=None,
        puede_gestionar=puede,
        entradas_lista=entradas,
    )


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
