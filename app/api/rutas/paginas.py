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
- `/detalle`, `/busqueda`, `/tablero`: la maqueta estatica dentro de la plantilla base,
  marcada "en construccion", hasta que lleguen H-18, H-19 y H-20.

Sin autenticacion hasta H-25.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape
from pydantic import ValidationError

from app.api.dependencias import AjustesDep, RepoAlertasDep, RepoReglasDep
from app.api.errores import ErrorCentinela
from app.api.esquemas import EdicionRegla
from app.api.fragmentos import ENTORNO as ENTORNO_FRAGMENTOS
from app.api.fragmentos import RUTA_PLANTILLAS
from app.api.rutas.reglas import ReglaIdRuta, editar_regla
from app.deteccion.evaluador import TIPOS_REGLA

enrutador = APIRouter(include_in_schema=False)

NAVEGACION = [
    ("/panel", "Panel", "panel"),
    ("/reglas", "Reglas", "reglas"),
    ("/detalle", "Detalle", "detalle"),
    ("/busqueda", "Buscar", "busqueda"),
    ("/tablero", "Tablero", "tablero"),
]

# Mayor gravedad primero. La severidad llega de Mongo en mayusculas.
RANGO_SEVERIDAD = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2}

RUTA_MAQUETAS = RUTA_PLANTILLAS / "maquetas"
ENLACES_MAQUETA = {
    "panel.html": "/panel",
    "detalle.html": "/detalle",
    "busqueda.html": "/busqueda",
    "tablero.html": "/tablero",
}

ENTORNO = Environment(
    loader=FileSystemLoader(str(RUTA_PLANTILLAS)),
    autoescape=select_autoescape(default_for_string=True, default=True),
    trim_blocks=True,
    lstrip_blocks=True,
)
# Los mismos filtros que usa el fragmento del canal: una fila se ve igual venga de donde venga.
ENTORNO.filters["hora_costa_rica"] = ENTORNO_FRAGMENTOS.filters["hora_costa_rica"]
ENTORNO.filters["colones"] = ENTORNO_FRAGMENTOS.filters["colones"]


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


def ordenar_cola(alertas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Severidad (critica primero) y luego fecha (la mas reciente primero)."""
    por_fecha = sorted(alertas, key=lambda a: a["fecha_creacion"], reverse=True)
    return sorted(por_fecha, key=lambda a: RANGO_SEVERIDAD.get(a["severidad"], 9))


async def _cola(repo: RepoAlertasDep, ajustes: AjustesDep) -> list[dict[str, Any]]:
    # `limite` acotado por app.pagina_maxima, igual que la API.
    limite = min(ajustes.app.pagina_por_omision, ajustes.app.pagina_maxima)
    alertas, _total = await repo.listar(
        estado=None, severidad=None, desde=None, hasta=None, limite=limite, desplazamiento=0
    )
    return ordenar_cola(alertas)


@enrutador.get("/")
async def raiz() -> RedirectResponse:
    return RedirectResponse("/panel")


@enrutador.get("/panel", response_class=HTMLResponse)
async def panel(repo: RepoAlertasDep, ajustes: AjustesDep) -> HTMLResponse:
    return _pagina("paginas/panel.html", "panel", alertas=await _cola(repo, ajustes))


@enrutador.get("/panel/cola", response_class=HTMLResponse)
async def cola(repo: RepoAlertasDep, ajustes: AjustesDep) -> HTMLResponse:
    """Solo las filas: lo que se pide ante `resincronizar` o al reconectar."""
    html = ENTORNO.get_template("paginas/cola.html").render(
        alertas=await _cola(repo, ajustes)
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
                raise ValueError("los parametros tienen que ser un objeto JSON")
            campos["parametros"] = decodificados
        if version_esperada.strip():
            campos["version_esperada"] = int(version_esperada)
    except json.JSONDecodeError as fallo:
        raise ValueError(f"los parametros no son JSON valido ({fallo.msg})") from fallo
    except ValueError as fallo:
        if "parametros" in str(fallo):
            raise
        raise ValueError(f"un numero no es valido ({fallo})") from fallo
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
            antes, mensaje="Los datos no son validos", es_error=True, detalles=detalles, estado=422
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
        return _tarjeta(actual, mensaje="Sin cambios: no se creo una version nueva")
    return _tarjeta(actual, mensaje=f"Guardada: ahora es la version {resultado.version}")


# ---------------------------------------------------------------------------
# Maquetas, hasta que existan H-18, H-19 y H-20
# ---------------------------------------------------------------------------


@cache
def _cuerpo_maqueta(archivo: str) -> str:
    """El `<main>` de la maqueta, con sus enlaces apuntando a las rutas del portal."""
    texto = Path(RUTA_MAQUETAS / archivo).read_text(encoding="utf-8")
    coincidencia = re.search(r"<main[^>]*>(.*)</main>", texto, flags=re.DOTALL)
    cuerpo = coincidencia.group(1) if coincidencia else ""
    for original, ruta in ENLACES_MAQUETA.items():
        cuerpo = cuerpo.replace(original, ruta)
    return cuerpo


def _ruta_maqueta(clave: str, titulo: str) -> None:
    @enrutador.get(f"/{clave}", response_class=HTMLResponse, name=f"pagina_{clave}")
    async def _pagina_maqueta() -> HTMLResponse:
        return _pagina(
            "paginas/maqueta.html",
            clave,
            titulo=titulo,
            cuerpo=_cuerpo_maqueta(f"{clave}.html"),
        )


_ruta_maqueta("detalle", "Detalle de alerta")
_ruta_maqueta("busqueda", "Busqueda")
_ruta_maqueta("tablero", "Tablero de indicadores")
