"""Lecturas JSON que necesitan las pantallas del portal (T-61), bajo `/api/v1`.

Cada pagina HTMX arma sus datos en el servidor; la SPA React necesita esos mismos datos
como JSON. Aqui no hay logica nueva: cada ruta llama a los mismos repositorios y a las
mismas funciones puras que la pagina equivalente, asi que las dos interfaces no pueden
divergir mientras convivan.

| Ruta | Pantalla | Funcion que comparte con HTMX |
|---|---|---|
| `GET /alertas/{alerta_id}/detalle` | `/detalle/{alerta_id}` | `app/api/detalle.py` |
| `GET /casos-destino` y `/casos-destino/{cuenta}` | `/panel` por cuenta destino | `app/api/casos.py` |
| `GET /deteccion/rezago` | aviso del panel | `antiguedad_legible` |
| `GET /tablero` | `/tablero` | `app/repos/tablero.py`, `app/api/graficos.py` |
| `GET /red-mulas/{cuenta}/sankey` | Sankey del detalle y de la red | `app/api/sankey.py` |
| `GET /red-mulas/{cuenta}/grafo` | `/red-mulas/{cuenta}` | `app/api/graficos.disponer_grafo` |

Los permisos de cada una estan en `app/seguridad/permisos.py`, como los de toda ruta.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from app.api.casos import acciones_del_caso, con_sugerencia
from app.api.dependencias import (
    AjustesDep,
    RepoAlertasDep,
    RepoCasosDestinoDep,
    RepoDetalleDep,
    RepoReglasDep,
    RepoTableroDep,
    SupervisorDep,
)
from app.api.detalle import (
    AVISO_V1,
    acciones_permitidas,
    caso_de,
    cortes_de,
    es_v2,
    explicacion_puntaje,
)
from app.api.errores import CodigoError, ErrorCentinela, respuestas_documentadas
from app.api.esquemas_portal import (
    AccionDisponible,
    AccionLote,
    AvisoRezago,
    CasoDestinoRespuesta,
    DetalleAlerta,
    PaginaCasosDestino,
    PoliticaDetalle,
    RespuestaGrafo,
    RespuestaSankey,
    RespuestaTablero,
)
from app.api.formatos import antiguedad_legible
from app.api.graficos import disponer_grafo, graficos_tablero
from app.api.paginacion import acotar_limite
from app.api.rutas.alertas import AlertaIdRuta
from app.api.rutas.inteligencia import CuentaRuta, RepoRedMulasDep
from app.api.rutas.listas_riesgo import CuentaSinpeRuta, RepoListasRiesgoDep
from app.api.sankey import disponer_sankey, victima_de
from app.api.sesion import SesionDep
from app.deteccion.d1 import NOMBRE_D1
from app.modelos import Alerta
from app.modelos.base import asumir_utc
from app.repos.casos_destino import CasoDestino
from app.repos.red_mulas import (
    SALTOS_MAXIMOS,
    VENTANA_MINUTOS,
    CuentaNoEncontrada,
    RedMulas,
    RepoRedMulas,
)
from app.repos.tablero import DIAS_MAXIMOS
from app.seguridad.permisos import Permiso, rol_tiene
from app.supervision.rezago import aviso_por_atraso, aviso_por_conteo

enrutador = APIRouter(tags=["portal"])

PERIODOS = (1, 7, 14, 30)
COMANDO_INDICADORES = "python tareas.py indicadores"
SIN_SALIDAS = "Esta cuenta no tiene salidas registradas en la ventana."

DesdeConsulta = Annotated[
    datetime | None, Query(description="Instante de partida (con zona; sin zona, UTC)")
]
VentanaConsulta = Annotated[
    int, Query(ge=1, le=1440, description="Minutos maximos entre que llega el dinero y sale")
]
SaltosConsulta = Annotated[
    int, Query(ge=1, le=SALTOS_MAXIMOS, description="Saltos maximos a recorrer")
]


# ---------------------------------------------------------------------------
# Detalle de alerta
# ---------------------------------------------------------------------------


@enrutador.get(
    "/alertas/{alerta_id}/detalle",
    response_model=DetalleAlerta,
    summary="Una alerta explicada: reglas con su aporte, cortes, contexto y acciones",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def detalle_alerta(
    alerta_id: AlertaIdRuta,
    repo: RepoAlertasDep,
    repo_detalle: RepoDetalleDep,
    repo_reglas: RepoReglasDep,
) -> DetalleAlerta:
    """Lo mismo que la pagina `/detalle/{alerta_id}`, calculado con las mismas funciones."""
    alerta = await repo.obtener(alerta_id)
    if alerta is None:
        raise ErrorCentinela(
            CodigoError.NO_ENCONTRADO, f"No existe la alerta {alerta_id}", status.HTTP_404_NOT_FOUND
        )
    vigente = await repo_reglas.politica_documento()
    politica, origen = cortes_de(alerta, vigente)
    contexto = await repo_detalle.contexto(alerta["transaccion_id"])
    tipos = {r["_id"]: r["tipo"] for r in await repo_reglas.listar(activa=None)}
    agentes = await repo_detalle.agentes()
    transaccion = contexto["transaccion"]
    cliente = contexto["cliente"]
    perfil = contexto["perfil"]
    v2 = es_v2(alerta)
    return DetalleAlerta(
        alerta=Alerta.model_validate(alerta),
        es_v2=v2,
        aviso_v1=None if v2 else AVISO_V1,
        transaccion=(
            {"transaccion_id": transaccion["_id"], **_sin_simulacion(transaccion)}
            if transaccion is not None
            else None
        ),
        cliente=(
            {"cliente_id": cliente["_id"], "nombre": cliente["nombre"], "cedula": cliente.get("cedula")}
            if cliente is not None
            else None
        ),
        perfil=perfil,
        politica=PoliticaDetalle(
            politica_id=vigente.get("_id") if vigente is not None else None,
            version=politica.get("version") if politica is not None else None,
            origen=origen,  # type: ignore[arg-type]
        ),
        puntaje=explicacion_puntaje(
            alerta, politica, tipos=tipos, transaccion=transaccion, perfil=perfil
        ),
        acciones=[
            AccionDisponible(destino=destino, rotulo=rotulo, ayuda=ayuda, cierra=cierra)  # type: ignore[arg-type]
            for destino, rotulo, _clase, ayuda, cierra in acciones_permitidas(alerta["estado"])
        ],
        caso_id=await caso_de(alerta, repo_detalle),
        nombres_agentes={a["_id"]: a["nombre"] for a in agentes},
    )


def _sin_simulacion(transaccion: dict[str, Any]) -> dict[str, Any]:
    """La transaccion sin `_id` (va como `transaccion_id`) ni la etiqueta oculta."""
    return {k: v for k, v in transaccion.items() if k not in ("_id", "simulacion")}


# ---------------------------------------------------------------------------
# Panel por cuenta destino y aviso de rezago
# ---------------------------------------------------------------------------


def _caso(caso: CasoDestino) -> CasoDestinoRespuesta:
    return CasoDestinoRespuesta(
        cuenta_destino=caso.cuenta_destino,
        total_alertas=caso.total_alertas,
        victimas=caso.victimas,
        monto_total_crc=caso.monto_total_crc,
        severidad_max=caso.severidad_max,  # type: ignore[arg-type]
        puntaje_max=caso.puntaje_max,
        mas_antigua=caso.mas_antigua,
        mas_reciente=caso.mas_reciente,
        reglas=[{"codigo": codigo, "veces": veces} for codigo, veces in caso.reglas],  # type: ignore[misc]
        alertas=caso.alertas,  # type: ignore[arg-type]
        alertas_ocultas=caso.alertas_ocultas,
        en_lista_riesgo=caso.en_lista_riesgo,
        sugerencia=caso.sugerencia,
        acciones=[
            AccionLote(destino=destino, rotulo=rotulo)  # type: ignore[arg-type]
            for destino, rotulo, _clase in acciones_del_caso(caso)
        ],
    )


@enrutador.get(
    "/casos-destino",
    response_model=PaginaCasosDestino,
    summary="Alertas abiertas agrupadas por cuenta destino, de mayor a menor severidad",
    responses=respuestas_documentadas(
        status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_500_INTERNAL_SERVER_ERROR
    ),
)
async def listar_casos_destino(
    ajustes: AjustesDep,
    repo: RepoCasosDestinoDep,
    limite: Annotated[
        int | None, Query(ge=1, description="Cuentas por pagina; se acota a app.pagina_maxima")
    ] = None,
    desplazamiento: Annotated[int, Query(ge=0)] = 0,
) -> PaginaCasosDestino:
    """La vista por omision del panel (D-56). Cada cuenta trae su sugerencia y sus acciones."""
    efectivo = acotar_limite(
        limite, maximo=ajustes.app.pagina_maxima, por_omision=ajustes.app.pagina_por_omision
    )
    casos, total = await repo.listar(limite=efectivo, desplazamiento=desplazamiento)
    return PaginaCasosDestino(
        total=total,
        limite=efectivo,
        desplazamiento=desplazamiento,
        elementos=[_caso(con_sugerencia(c)) for c in casos],
    )


@enrutador.get(
    "/casos-destino/{cuenta}",
    response_model=CasoDestinoRespuesta,
    summary="Las alertas abiertas hacia una cuenta destino",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def obtener_caso_destino(
    cuenta: CuentaSinpeRuta, repo: RepoCasosDestinoDep
) -> CasoDestinoRespuesta:
    """404 si la cuenta ya no tiene alertas abiertas: la SPA la quita de la lista."""
    caso = await repo.obtener(cuenta)
    if caso is None:
        raise ErrorCentinela(
            CodigoError.NO_ENCONTRADO,
            f"La cuenta {cuenta} no tiene alertas abiertas",
            status.HTTP_404_NOT_FOUND,
        )
    return _caso(con_sugerencia(caso))


@enrutador.get(
    "/deteccion/rezago",
    response_model=AvisoRezago,
    summary="Aviso de D1 atrasado, con la ultima medicion del supervisor",
)
async def aviso_rezago(supervisor: SupervisorDep, ajustes: AjustesDep) -> AvisoRezago:
    """No consulta la base: lee lo que D1 dejo en el supervisor, igual que `/estado`."""
    medida = None
    try:
        medida = supervisor.informe().tarea(NOMBRE_D1).rezago
    except KeyError:
        pass
    desde = ajustes.deteccion.rezago_aviso_desde
    atraso_desde = ajustes.deteccion.rezago_aviso_atraso_segundos
    if medida is None:
        return AvisoRezago(atrasada=False, aviso_desde=desde, aviso_atraso_desde=atraso_desde)
    por_conteo = aviso_por_conteo(medida, desde)
    return AvisoRezago(
        atrasada=por_conteo or aviso_por_atraso(medida, atraso_desde),
        por_conteo=por_conteo,
        pendientes=medida.pendientes,
        aviso_desde=desde,
        atraso_segundos=medida.atraso_segundos,
        aviso_atraso_desde=atraso_desde,
        atraso_legible=antiguedad_legible(medida.atraso_segundos),
        segundos_para_ponerse_al_dia=medida.segundos_para_ponerse_al_dia,
        estimado=antiguedad_legible(medida.segundos_para_ponerse_al_dia),
        medido_en=medida.medido_en,
    )


# ---------------------------------------------------------------------------
# Tablero
# ---------------------------------------------------------------------------


@enrutador.get(
    "/tablero",
    response_model=RespuestaTablero,
    summary="Indicadores del periodo contra el anterior, con la geometria de sus graficos",
    responses=respuestas_documentadas(
        status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_500_INTERNAL_SERVER_ERROR
    ),
)
async def tablero(
    repo: RepoTableroDep,
    dias: Annotated[int, Query(ge=1, le=DIAS_MAXIMOS, description="Largo del periodo")] = 7,
) -> RespuestaTablero:
    """Lee `indicadores_diarios`; no recalcula nada en la peticion."""
    resumen = await repo.leer(dias)
    return RespuestaTablero(
        dias=dias,
        periodos=list(PERIODOS),
        comando_recalculo=COMANDO_INDICADORES,
        resumen=resumen,
        graficos=None if resumen["vacio"] else graficos_tablero(resumen),
    )


# ---------------------------------------------------------------------------
# Red de mulas: Sankey y grafo
# ---------------------------------------------------------------------------


async def _recorrer(
    repo: RepoRedMulas, cuenta: str, desde: datetime | None, ventana: int, saltos: int
) -> RedMulas:
    try:
        return await repo.recorrer(
            cuenta,
            desde=asumir_utc(desde) if desde is not None else None,
            ventana_minutos=ventana,
            saltos=saltos,
        )
    except CuentaNoEncontrada as fallo:
        raise ErrorCentinela(
            CodigoError.NO_ENCONTRADO, str(fallo), status.HTTP_404_NOT_FOUND
        ) from fallo


_ERRORES_RED = respuestas_documentadas(
    status.HTTP_404_NOT_FOUND,
    status.HTTP_422_UNPROCESSABLE_CONTENT,
    status.HTTP_500_INTERNAL_SERVER_ERROR,
)


@enrutador.get(
    "/red-mulas/{cuenta}/sankey",
    response_model=RespuestaSankey,
    summary="Sankey del recorrido del dinero, ya dispuesto: nodos, bandas y columnas",
    responses=_ERRORES_RED,
)
async def sankey(
    cuenta: CuentaRuta,
    repo: RepoRedMulasDep,
    repo_detalle: RepoDetalleDep,
    desde: DesdeConsulta = None,
    ventana_minutos: VentanaConsulta = VENTANA_MINUTOS,
    saltos: SaltosConsulta = SALTOS_MAXIMOS,
    transaccion: Annotated[
        str | None, Query(description="Transaccion de partida: su cliente es la victima")
    ] = None,
) -> RespuestaSankey:
    """El grosor de cada banda es un entero proporcional a los colones (`grosor_px`)."""
    red = await _recorrer(repo, cuenta, desde, ventana_minutos, saltos)
    if not red.aristas:
        return RespuestaSankey(cuenta=cuenta, sankey=None, motivo=SIN_SALIDAS)
    disposicion = disponer_sankey(red, await victima_de(repo_detalle, transaccion))
    return RespuestaSankey(
        cuenta=cuenta,
        sankey=asdict(disposicion) if disposicion is not None else None,  # type: ignore[arg-type]
    )


@enrutador.get(
    "/red-mulas/{cuenta}/grafo",
    response_model=RespuestaGrafo,
    summary="La red de mulas con la posicion de cada nodo y arista",
    responses=_ERRORES_RED,
)
async def grafo(
    cuenta: CuentaRuta,
    repo: RepoRedMulasDep,
    listas: RepoListasRiesgoDep,
    sesion: SesionDep,
    desde: DesdeConsulta = None,
    ventana_minutos: VentanaConsulta = VENTANA_MINUTOS,
    saltos: SaltosConsulta = SALTOS_MAXIMOS,
) -> RespuestaGrafo:
    """Las entradas de `listas_riesgo` solo van a quien puede darlas de baja."""
    red = await _recorrer(repo, cuenta, desde, ventana_minutos, saltos)
    entradas: list[dict[str, Any]] = []
    en_lista = [nodo.cuenta for nodo in red.nodos if nodo.en_lista_riesgo]
    if en_lista and rol_tiene(sesion.rol, Permiso.GESTIONAR_LISTAS):
        por_cuenta = await listas.entradas_de_cuentas(en_lista)
        entradas = [e for sinpe in en_lista for e in por_cuenta.get(sinpe, [])]
    return RespuestaGrafo(
        red=red,
        grafo=disponer_grafo(red) if red.aristas else None,  # type: ignore[arg-type]
        entradas_lista=entradas,  # type: ignore[arg-type]
    )
