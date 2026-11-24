"""Las paginas HTML del portal (Jinja + HTMX). Viven fuera de `/api/v1`.

El portal no inventa un contrato propio: las paginas llaman a los mismos repositorios
que la API, por las mismas dependencias, y la edicion de reglas reutiliza el handler de
`PATCH /api/v1/reglas/{id}` (version esperada, 409, validacion de parametros), asi que
las dos puertas no pueden divergir.

- `/panel` (H-17): la cola se pinta del servidor y luego el canal SSE la mantiene. El
  evento `resincronizar` (y una reconexion) vuelven a pedir `/panel/cola`: se
  reconstruye la cola, no se repiten eventos. Es un fragmento HTML y no el JSON de
  `GET /api/v1/alertas` porque HTMX inserta HTML.
- `/reglas` (T-16): catalogo, edicion con concurrencia optimista, historia de versiones,
  tipos soportados y politica vigente en solo lectura.
- `/detalle/{alerta_id}` (H-18): la transaccion, el perfil, cada regla disparada con su
  aporte, la politica, el historial y la resolucion desde ahi. Resolver llama al mismo
  handler que `POST /api/v1/alertas/{id}/transiciones`, asi que las dos puertas no
  divergen: misma maquina de estados, mismo 409.
- `/detalle` (sin id) redirige a `/panel`: el detalle se abre desde una alerta.
  `/busqueda` vive en `paginas_busqueda.py` (H-19) y `/tablero` en `paginas_tablero.py` (H-20).

Todas las paginas exigen sesion (H-25): el permiso de cada una esta en
`app/seguridad/permisos.py` y lo aplica el guardia de `app/api/sesion.py`.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape
from pydantic import ValidationError

from app.api.casos import acciones_del_caso, casos_para_panel
from app.api.dependencias import (
    AjustesDep,
    RepoAlertasDep,
    RepoCasosDestinoDep,
    RepoDetalleDep,
    RepoReglasDep,
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
from app.api.errores import ErrorCentinela
from app.api.esquemas import EdicionRegla, PeticionTransicion
from app.api.formatos import antiguedad_legible, etiqueta
from app.api.formatos import registrar as registrar_formatos
from app.api.fragmentos import RUTA_PLANTILLAS
from app.api.rutas.alertas import AlertaIdRuta, transicionar_alerta
from app.api.rutas.reglas import ReglaIdRuta, editar_regla
from app.api.sesion import SesionDep, permite_ruta, sesion_actual
from app.deteccion.d1 import NOMBRE_D1
from app.deteccion.evaluador import TIPOS_REGLA
from app.modelos import (
    EstadoAlerta,
    TransicionInvalida,
)

enrutador = APIRouter(include_in_schema=False)

ID_ALERTA = re.compile(r"^ALR-\d{3,}$")

NAVEGACION = [
    ("/panel", "Panel", "panel"),
    ("/reglas", "Reglas", "reglas"),
    ("/busqueda", "Búsqueda", "busqueda"),
    ("/tablero", "Tablero", "tablero"),
]

ENTORNO = Environment(
    loader=FileSystemLoader(str(RUTA_PLANTILLAS)),
    autoescape=select_autoescape(default_for_string=True, default=True),
    trim_blocks=True,
    lstrip_blocks=True,
)
# Los mismos filtros que usa el fragmento del canal: una fila se ve igual venga de donde venga.
registrar_formatos(ENTORNO)
# Para la barra de navegacion y el encabezado: que ve cada rol, y quien es.
ENTORNO.globals["permite_ruta"] = permite_ruta
ENTORNO.globals["sesion_actual"] = sesion_actual
ENTORNO.globals["acciones_del_caso"] = acciones_del_caso

# Se reexporta: `pruebas/contrato/prueba_aviso_rezago.py` la importa desde aqui.
__all__ = ["antiguedad_legible"]


def _json_compacto(valor: Any) -> str:
    """Los parametros como JSON; una regla sin ellos (seeds viejas) muestra `{}`."""
    if isinstance(valor, Undefined):
        valor = {}
    return json.dumps(valor, ensure_ascii=False, sort_keys=True)


ENTORNO.filters["json_compacto"] = _json_compacto


def _pagina(plantilla: str, activa: str, estado: int = 200, **contexto: Any) -> HTMLResponse:
    html = ENTORNO.get_template(plantilla).render(
        navegacion=NAVEGACION, activa=activa, **contexto
    )
    return HTMLResponse(html, status_code=estado)


def pagina_prohibida(mensaje: str) -> HTMLResponse:
    """403 del portal: la pagina de acceso denegado, dentro del marco normal."""
    return _pagina("paginas/prohibido.html", "", estado=403, mensaje=mensaje)


async def _cola(repo: RepoAlertasDep, ajustes: AjustesDep) -> list[dict[str, Any]]:
    # `limite` acotado por app.pagina_maxima, igual que la API.
    limite = min(ajustes.app.pagina_por_omision, ajustes.app.pagina_maxima)
    alertas, _total = await repo.listar(
        estado=None, severidad=None, desde=None, hasta=None, limite=limite, desplazamiento=0
    )
    # El orden (puntaje, fecha, _id) ya viene de la base: ORDEN_PANEL.
    return alertas


@enrutador.get("/")
async def raiz() -> RedirectResponse:
    return RedirectResponse("/panel")


VISTAS_PANEL = ("alertas", "casos")
# D-56: por omision se agrupa por cuenta destino (la mula), que es lo que deja decidir una
# vez con todo el contexto. La vista por alerta queda a un clic y la eleccion se recuerda
# en una cookie. En la interfaz se dice "Por cuenta destino" y "Por alerta": "caso" a
# secas es la coleccion `casos` (CAS-xxx, la escalacion).
VISTA_POR_OMISION = "casos"
COOKIE_VISTA = "centinela_vista"


@enrutador.get("/panel", response_class=HTMLResponse)
async def panel(
    peticion: Request,
    repo: RepoAlertasDep,
    repo_casos: RepoCasosDestinoDep,
    ajustes: AjustesDep,
    vista: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    elegida = vista if vista in VISTAS_PANEL else peticion.cookies.get(COOKIE_VISTA)
    if elegida not in VISTAS_PANEL:
        elegida = VISTA_POR_OMISION
    if elegida == "casos":
        limite = min(ajustes.app.pagina_por_omision, ajustes.app.pagina_maxima)
        casos, total = await casos_para_panel(repo_casos, limite)
        respuesta = _pagina(
            "paginas/panel.html", "panel", vista="casos", casos=casos, total_casos=total
        )
    else:
        respuesta = _pagina(
            "paginas/panel.html", "panel", vista="alertas", alertas=await _cola(repo, ajustes)
        )
    if vista in VISTAS_PANEL:
        respuesta.set_cookie(COOKIE_VISTA, vista, max_age=60 * 60 * 24 * 365, samesite="lax", httponly=True)
    return respuesta


@enrutador.get("/panel/cola", response_class=HTMLResponse)
async def cola(repo: RepoAlertasDep, ajustes: AjustesDep) -> HTMLResponse:
    """Solo las filas: lo que se pide ante `resincronizar` o al reconectar."""
    html = ENTORNO.get_template("paginas/cola.html").render(
        alertas=await _cola(repo, ajustes)
    )
    return HTMLResponse(html)


@enrutador.get("/panel/rezago", response_class=HTMLResponse)
async def rezago(supervisor: SupervisorDep, ajustes: AjustesDep) -> HTMLResponse:
    """Aviso de D1 atrasado (T-57). No consulta la base: lee la ultima medicion que D1
    dejo en el supervisor, igual que `/estado`. Vacio si D1 esta al dia o sin medir."""
    medida = None
    try:
        medida = supervisor.informe().tarea(NOMBRE_D1).rezago
    except KeyError:
        pass
    atrasada = medida is not None and medida.pendientes > ajustes.deteccion.rezago_aviso_desde
    html = ENTORNO.get_template("paginas/aviso_rezago.html").render(
        atrasada=atrasada,
        pendientes=f"{medida.pendientes:,}".replace(",", " ") if medida else 0,
        estimado=antiguedad_legible(medida.segundos_para_ponerse_al_dia) if medida else "",
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------


@enrutador.get("/reglas", response_class=HTMLResponse)
async def reglas(repo: RepoReglasDep) -> HTMLResponse:
    return _pagina(
        "paginas/reglas.html",
        "reglas",
        reglas=await repo.listar(activa=None),
        politica=await repo.politica_vigente(),
        tipos=list(TIPOS_REGLA.items()),
    )


def _tarjeta(
    regla: dict[str, Any],
    *,
    mensaje: str | None = None,
    es_error: bool = False,
    detalles: list[str] | None = None,
    estado: int = 200,
) -> HTMLResponse:
    html = ENTORNO.get_template("paginas/regla_tarjeta.html").render(
        regla=regla, mensaje=mensaje, es_error=es_error, detalles=detalles or []
    )
    return HTMLResponse(html, status_code=estado)


def _edicion_desde_formulario(
    umbral: str, peso: str, activa: str, parametros: str, version_esperada: str
) -> EdicionRegla:
    """El formulario manda texto; aca se convierte a lo que la API espera.

    Un campo vacio es "no tocar". Levanta `ValueError` o `ValidationError` con un
    mensaje que se muestra tal cual.
    """
    campos: dict[str, Any] = {}
    try:
        if umbral.strip():
            campos["umbral"] = float(umbral)
        if peso.strip():
            campos["peso"] = int(peso)
        if parametros.strip():
            decodificados = json.loads(parametros)
            if not isinstance(decodificados, dict):
                raise ValueError("los parámetros tienen que ser un objeto JSON")
            campos["parametros"] = decodificados
        if version_esperada.strip():
            campos["version_esperada"] = int(version_esperada)
    except json.JSONDecodeError as fallo:
        raise ValueError(f"los parámetros no son JSON válido ({fallo.msg})") from fallo
    except ValueError as fallo:
        if "parámetros" in str(fallo):
            raise
        raise ValueError(f"un número no es válido ({fallo})") from fallo
    if activa in ("true", "false"):
        campos["activa"] = activa == "true"
    return EdicionRegla(**campos)


@enrutador.post("/reglas/{regla_id}", response_class=HTMLResponse)
async def guardar_regla(
    regla_id: ReglaIdRuta, peticion: Request, repo: RepoReglasDep
) -> HTMLResponse:
    """Aplica la edicion con el mismo handler que la API y devuelve la tarjeta.

    Si falla (409 por version, 422 por validacion) la tarjeta vuelve con la regla tal
    como esta ahora en la base y el mensaje del error, no con lo que se intento.
    """
    # El cuerpo es `application/x-www-form-urlencoded`. Se lee con la biblioteca
    # estandar: `Form()` de FastAPI exige `python-multipart`, que no es dependencia.
    cuerpo = parse_qs((await peticion.body()).decode("utf-8"), keep_blank_values=True)

    def campo(nombre: str) -> str:
        return cuerpo.get(nombre, [""])[0]

    antes = await repo.obtener(regla_id)
    if antes is None:
        return HTMLResponse(
            f'<div class="mensaje mensaje-error" role="alert">No existe la regla {regla_id}</div>',
            status_code=404,
        )
    try:
        edicion = _edicion_desde_formulario(
            campo("umbral"),
            campo("peso"),
            campo("activa"),
            campo("parametros"),
            campo("version_esperada"),
        )
        resultado = await editar_regla(regla_id, edicion, repo)
    except ValidationError as fallo:
        detalles = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in fallo.errors()]
        return _tarjeta(
            antes, mensaje="Los datos no son válidos", es_error=True, detalles=detalles, estado=422
        )
    except ValueError as fallo:
        return _tarjeta(antes, mensaje=str(fallo), es_error=True, estado=422)
    except ErrorCentinela as fallo:
        actual = await repo.obtener(regla_id) or antes
        detalles = [
            f"{d.campo}: {d.mensaje}" if d.campo else d.mensaje for d in fallo.detalles
        ]
        return _tarjeta(
            actual,
            mensaje=f"{fallo.estado_http}: {fallo.mensaje}",
            es_error=True,
            detalles=detalles,
            estado=fallo.estado_http,
        )

    actual = await repo.obtener(regla_id) or antes
    if resultado.version == antes.get("version"):
        return _tarjeta(actual, mensaje="Sin cambios: no se creó una versión nueva")
    return _tarjeta(actual, mensaje=f"Guardada: ahora es la versión {resultado.version}")


# ---------------------------------------------------------------------------
# Detalle de alerta (H-18)
# ---------------------------------------------------------------------------

def _contexto_acciones(
    alerta: dict[str, Any],
    agentes: list[dict[str, Any]],
    *,
    caso_id: str | None,
    mensaje: str | None = None,
    es_error: bool = False,
    oob: bool = False,
) -> dict[str, Any]:
    nombres = {a["_id"]: a["nombre"] for a in agentes}
    return {
        "alerta": alerta,
        "estado_alerta": EstadoAlerta(alerta["estado"]).value,
        "botones": acciones_permitidas(alerta["estado"]),
        "nombres": nombres,
        "caso_id": caso_id,
        "mensaje": mensaje,
        "es_error": es_error,
        "oob": oob,
    }


@enrutador.get("/detalle")
async def detalle_sin_id() -> RedirectResponse:
    """El detalle se abre desde una alerta: sin id no hay nada que mostrar, se vuelve al panel."""
    return RedirectResponse("/panel")


@enrutador.get("/detalle/{alerta_id}", response_class=HTMLResponse)
async def detalle(
    alerta_id: str,
    repo: RepoAlertasDep,
    repo_detalle: RepoDetalleDep,
    repo_reglas: RepoReglasDep,
) -> HTMLResponse:
    # Un id mal formado es una alerta que no existe, no un 422 de validacion: la
    # pagina responde con la plantilla amable en los dos casos.
    alerta = await repo.obtener(alerta_id) if ID_ALERTA.match(alerta_id) else None
    if alerta is None:
        return _pagina(
            "paginas/detalle_no_existe.html", "detalle", estado=404, alerta_id=alerta_id
        )
    vigente = await repo_reglas.politica_documento()
    politica, origen_cortes = cortes_de(alerta, vigente)
    contexto = await repo_detalle.contexto(alerta["transaccion_id"])
    agentes = await repo_detalle.agentes()
    tipos = {r["_id"]: r["tipo"] for r in await repo_reglas.listar(activa=None)}
    return _pagina(
        "paginas/detalle.html",
        "detalle",
        es_v2=es_v2(alerta),
        aviso_v1=AVISO_V1,
        transaccion=contexto["transaccion"],
        cliente=contexto["cliente"],
        perfil=contexto["perfil"],
        politica=politica,
        politica_usada=alerta.get("politica"),
        origen_cortes=origen_cortes,
        puntaje=explicacion_puntaje(
            alerta,
            politica,
            tipos=tipos,
            transaccion=contexto["transaccion"],
            perfil=contexto["perfil"],
        ),
        **_contexto_acciones(alerta, agentes, caso_id=await caso_de(alerta, repo_detalle)),
    )


def _fragmento_acciones(contexto: dict[str, Any], estado: int = 200) -> HTMLResponse:
    html = ENTORNO.get_template("paginas/detalle_acciones.html").render(**contexto)
    return HTMLResponse(html, status_code=estado)


@enrutador.post("/detalle/{alerta_id}/transicion", response_class=HTMLResponse)
async def resolver_desde_detalle(
    alerta_id: AlertaIdRuta,
    peticion: Request,
    repo: RepoAlertasDep,
    repo_detalle: RepoDetalleDep,
    sesion: SesionDep,
) -> HTMLResponse:
    """Mueve la alerta con el mismo handler que la API y devuelve la seccion de acciones.

    El agente que queda en el historial es el de la sesion: el formulario ya no lo
    manda, y si lo mandara se ignora. Un 409 (otro agente la
    tomo, la transicion no esta en la tabla) vuelve como mensaje sobre la alerta tal
    como esta ahora en la base, no como lo que el agente veia.
    """
    cuerpo = parse_qs((await peticion.body()).decode("utf-8"), keep_blank_values=True)

    def campo(nombre: str) -> str:
        return cuerpo.get(nombre, [""])[0]

    agentes = await repo_detalle.agentes()
    actual = await repo.obtener(alerta_id)
    if actual is None:
        return HTMLResponse(
            f'<div class="mensaje mensaje-error" role="alert">No existe la alerta {alerta_id}</div>',
            status_code=404,
        )

    mensaje: str | None = None
    caso_id: str | None = None
    estado_http = 200
    try:
        pedida = PeticionTransicion(
            destino=campo("destino"),  # type: ignore[arg-type]
            agente_id=sesion.agente_id,
            nota=campo("nota").strip(),
            estado_esperado=campo("estado_esperado") or None,  # type: ignore[arg-type]
        )
        resultado = await transicionar_alerta(alerta_id, pedida, repo, sesion)
        caso_id = resultado.caso_id
        mensaje = f"Listo: la alerta pasó a {etiqueta(pedida.destino.value)}"
    except ValidationError as fallo:
        estado_http = 422
        mensaje = "Los datos no son válidos: " + "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in fallo.errors()
        )
    except ErrorCentinela as fallo:
        estado_http = fallo.estado_http
        mensaje = f"{fallo.estado_http}: {fallo.mensaje}"
    except TransicionInvalida as fallo:
        estado_http = 409
        mensaje = f"409: {fallo}"

    despues = await repo.obtener(alerta_id) or actual
    if caso_id is None:
        caso_id = await caso_de(despues, repo_detalle)
    return _fragmento_acciones(
        _contexto_acciones(
            despues,
            agentes,
            caso_id=caso_id,
            mensaje=mensaje,
            es_error=estado_http != 200,
            oob=True,
        ),
        estado=estado_http,
    )
