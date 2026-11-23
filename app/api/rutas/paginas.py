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
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape
from pydantic import ValidationError

from app.api.dependencias import (
    AjustesDep,
    RepoAlertasDep,
    RepoDetalleDep,
    RepoReglasDep,
)
from app.api.errores import ErrorCentinela
from app.api.esquemas import EdicionRegla, PeticionTransicion
from app.api.formatos import colones, decimal, etiqueta
from app.api.formatos import registrar as registrar_formatos
from app.api.fragmentos import RUTA_PLANTILLAS
from app.api.rutas.alertas import AlertaIdRuta, transicionar_alerta
from app.api.rutas.reglas import ReglaIdRuta, editar_regla
from app.api.sesion import SesionDep, permite_ruta, sesion_actual
from app.deteccion.evaluador import TIPOS_REGLA
from app.modelos import (
    ZONA_COSTA_RICA,
    EstadoAlerta,
    TransicionInvalida,
    transiciones_validas,
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

# Orden y rotulo de los botones. Cuales aparecen lo decide la maquina de estados.
# Cuarto elemento: ayuda corta del boton. Quinto: si la accion cierra la alerta y pide confirmar.
ACCIONES = [
    (
        EstadoAlerta.EN_REVISION,
        "Tomar",
        "boton-primario",
        "Te asigna la alerta y la pasa a En revisión.",
        False,
    ),
    (
        EstadoAlerta.CONFIRMADA,
        "Confirmar fraude",
        "boton-confirmar",
        "Cierra la alerta como fraude real. No se puede deshacer.",
        True,
    ),
    (
        EstadoAlerta.DESCARTADA,
        "Descartar",
        "boton-descartar",
        "Cierra la alerta como falso positivo. No se puede deshacer.",
        True,
    ),
    (
        EstadoAlerta.ESCALADA,
        "Escalar a caso",
        "boton-escalar",
        "Abre un caso de investigación y cierra la alerta. No se puede deshacer.",
        True,
    ),
]

AVISO_V1 = "alerta v1: migrar con python tareas.py migrar"


def _porcentaje(valor: int, total: int) -> int:
    return max(0, min(100, round(valor * 100 / total))) if total > 0 else 0


_PREFIJO_REGLA = re.compile(r"^\s*R-\d+\s+v\d+\s*:\s*")
_APORTE_INTERNO = re.compile(r"\s*\(aporta\s+\d+[^)]*\)")
_RUIDO_MIGRACION = re.compile(r";?\s*explicaci[oó]n reconstruida por la migraci[oó]n 001", re.IGNORECASE)


def _limpiar_explicacion(texto: str) -> str:
    """Quita lo que la tabla ya muestra en otra columna: "R-01 v2:", "(aporta 35)" y la nota de migracion."""
    limpio = _PREFIJO_REGLA.sub("", texto)
    limpio = _APORTE_INTERNO.sub("", limpio)
    limpio = _RUIDO_MIGRACION.sub("", limpio).strip(" ;")
    return limpio[:1].upper() + limpio[1:]


def _evidencia(
    tipo: str | None,
    alerta: dict[str, Any],
    transaccion: dict[str, Any] | None,
    perfil: dict[str, Any] | None,
) -> str | None:
    """Una linea calculada con datos que la pagina ya muestra. None si no alcanza para calcularla."""
    if perfil is None:
        return None
    if tipo == "monto":
        promedio = perfil.get("promedio_crc")
        if isinstance(promedio, int) and promedio > 0:
            veces = decimal(alerta["monto_crc"] / promedio)
            return (
                f"Monto {colones(alerta['monto_crc'])} contra un promedio de "
                f"{colones(promedio)}: {veces}x."
            )
    if tipo == "destino":
        frecuentes = [str(c) for c in perfil.get("destinos_frecuentes") or []]
        destino = alerta["cuenta_destino"]
        if destino not in frecuentes:
            return (
                f"El destino {destino} no figura entre los {len(frecuentes)} destinos "
                "frecuentes del cliente."
            )
    if tipo == "horario":
        horario = perfil.get("horario_habitual") or {}
        momento = (transaccion or {}).get("fecha") or alerta["fecha_creacion"]
        if horario.get("inicio") and horario.get("fin"):
            local = momento.astimezone(ZONA_COSTA_RICA)
            if not horario["inicio"] <= f"{local:%H:%M}" <= horario["fin"]:
                return (
                    f"{local:%H:%M} (hora CR) está fuera del horario habitual "
                    f"{horario['inicio']}-{horario['fin']}."
                )
    return None


def _cortes_de(
    alerta: dict[str, Any], vigente: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, str]:
    """La politica con que se calculo la alerta, y de donde salieron los cortes.

    Devuelve `(documento, origen)`: `usada` si es la politica que la alerta registra (la
    vigente o una version anterior de su historia); `vigente` si la alerta no registra
    politica o su version ya no esta disponible (los cortes son los de ahora, y la
    pagina lo rotula); `ninguna` si no hay politica en la base.
    """
    if vigente is None:
        return None, "ninguna"
    usada = alerta.get("politica")
    if not usada:
        return vigente, "vigente"
    if usada["politica_id"] == vigente.get("_id") and usada["version"] == vigente.get("version"):
        return vigente, "usada"
    if usada["politica_id"] == vigente.get("_id"):
        for anterior in vigente.get("versiones_anteriores") or []:
            if anterior.get("version") == usada["version"] and anterior.get("cortes_severidad"):
                return anterior, "usada"
    return vigente, "vigente"


def _explicacion_puntaje(
    alerta: dict[str, Any],
    politica: dict[str, Any] | None,
    *,
    tipos: dict[str, str],
    transaccion: dict[str, Any] | None,
    perfil: dict[str, Any] | None,
) -> dict[str, Any]:
    """Los aportes en orden, el acumulado de cada uno y el corte que alcanza el puntaje.

    Solo v2 trae aporte por regla; en v1 la lista es de ids y no hay nada que sumar.
    `politica` es el documento cuyos cortes se muestran (ver `_cortes_de`).
    """
    puntaje = alerta["puntaje"]
    maximo = politica["puntaje_maximo"] if politica is not None else 100
    acumulado = 0
    filas = []
    for regla in alerta["reglas_disparadas"]:
        if not isinstance(regla, dict):
            continue
        acumulado += regla["peso"]
        filas.append(
            {
                **regla,
                "explicacion": _limpiar_explicacion(regla["explicacion"]),
                "evidencia": _evidencia(
                    tipos.get(regla["regla_id"]), alerta, transaccion, perfil
                ),
                "acumulado": acumulado,
                "ancho": _porcentaje(regla["peso"], maximo),
            }
        )
    cortes = []
    if politica is not None:
        for corte in politica["cortes_severidad"]:
            cortes.append(
                {
                    "severidad": str(corte["severidad"]),
                    "minimo": corte["puntaje_minimo"],
                    "maximo": corte["puntaje_maximo"],
                    "inicio": _porcentaje(corte["puntaje_minimo"], maximo),
                    "alcanzado": corte["puntaje_minimo"] <= puntaje <= corte["puntaje_maximo"],
                }
            )
    return {
        "filas": filas,
        "suma": acumulado,
        "tope": acumulado > puntaje,
        "maximo": maximo,
        "cortes": cortes,
        "ancho_puntaje": _porcentaje(puntaje, maximo),
        "umbral": politica["umbral_alerta"] if politica is not None else None,
    }


def _contexto_acciones(
    alerta: dict[str, Any],
    agentes: list[dict[str, Any]],
    *,
    caso_id: str | None,
    mensaje: str | None = None,
    es_error: bool = False,
    oob: bool = False,
) -> dict[str, Any]:
    estado = EstadoAlerta(alerta["estado"])
    permitidas = transiciones_validas(estado)
    nombres = {a["_id"]: a["nombre"] for a in agentes}
    return {
        "alerta": alerta,
        "estado_alerta": estado.value,
        "botones": [
            (e.value, rotulo, clase, ayuda, terminal)
            for e, rotulo, clase, ayuda, terminal in ACCIONES
            if e in permitidas
        ],
        "nombres": nombres,
        "caso_id": caso_id,
        "mensaje": mensaje,
        "es_error": es_error,
        "oob": oob,
    }


async def _caso_de(alerta: dict[str, Any], repo_detalle: RepoDetalleDep) -> str | None:
    if alerta["estado"] != EstadoAlerta.ESCALADA.value:
        return None
    caso = await repo_detalle.caso_de_alerta(alerta["_id"])
    return caso["_id"] if caso is not None else None


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
    politica, origen_cortes = _cortes_de(alerta, vigente)
    contexto = await repo_detalle.contexto(alerta["transaccion_id"])
    agentes = await repo_detalle.agentes()
    tipos = {r["_id"]: r["tipo"] for r in await repo_reglas.listar(activa=None)}
    es_v2 = bool(alerta["reglas_disparadas"]) and all(
        isinstance(r, dict) for r in alerta["reglas_disparadas"]
    )
    return _pagina(
        "paginas/detalle.html",
        "detalle",
        es_v2=es_v2,
        aviso_v1=AVISO_V1,
        transaccion=contexto["transaccion"],
        cliente=contexto["cliente"],
        perfil=contexto["perfil"],
        politica=politica,
        politica_usada=alerta.get("politica"),
        origen_cortes=origen_cortes,
        puntaje=_explicacion_puntaje(
            alerta,
            politica,
            tipos=tipos,
            transaccion=contexto["transaccion"],
            perfil=contexto["perfil"],
        ),
        **_contexto_acciones(alerta, agentes, caso_id=await _caso_de(alerta, repo_detalle)),
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
        caso_id = await _caso_de(despues, repo_detalle)
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
