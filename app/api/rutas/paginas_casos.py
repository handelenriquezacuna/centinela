"""Vista por casos del panel (T-56): alertas abiertas agrupadas por cuenta destino.

- `GET /panel/casos`: todos los casos (lo que se pide ante `resincronizar`).
- `GET /panel/casos/{cuenta}`: el fragmento de UN caso. El SSE lo pide por cada alerta que
  llega: se suma al caso o lo crea, sin recargar los demas (y sin perder la seleccion).
- `POST /panel/lote`: aplica una transicion a las alertas marcadas, una por una, con la
  misma funcion que la API (`aplicar_lote`); devuelve el caso con el resultado arriba.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.api.casos import casos_para_panel, con_sugerencia
from app.api.dependencias import AjustesDep, RepoAlertasDep, RepoCasosDestinoDep
from app.api.esquemas import PeticionLote, ResultadoLote
from app.api.formatos import etiqueta
from app.api.rutas.alertas import aplicar_lote
from app.api.rutas.paginas import ENTORNO
from app.api.sesion import SesionDep

enrutador = APIRouter(include_in_schema=False)

CUENTA = re.compile(r"^\d{4}-\d{4}$")


def _limite(ajustes: AjustesDep) -> int:
    return min(ajustes.app.pagina_por_omision, ajustes.app.pagina_maxima)


def _render(plantilla: str, **contexto: Any) -> HTMLResponse:
    return HTMLResponse(ENTORNO.get_template(plantilla).render(**contexto))


def _motivo(item: Any, destino: str) -> str:
    """Por que una alerta no paso, en palabras del agente."""
    actual = etiqueta(item.estado_actual) if item.estado_actual else ""
    if item.codigo == "no_encontrado":
        return "ya no existe"
    if item.codigo == "transicion_invalida":
        return f"no puede pasar de {actual} a {etiqueta(destino)}"
    if item.codigo == "conflicto":
        return f"la tomó o resolvió otro agente (ahora está {actual.lower()})"
    return item.mensaje


def _resumen_lote(resultado: ResultadoLote, destino: str) -> dict[str, Any]:
    return {
        "aplicadas": resultado.aplicadas,
        "solicitadas": resultado.solicitadas,
        "destino": etiqueta(destino),
        "rechazadas": [
            {"alerta_id": r.alerta_id, "estado_http": r.estado_http, "motivo": _motivo(r, destino)}
            for r in resultado.resultados
            if not r.aplicada
        ],
        "casos_abiertos": list(dict.fromkeys(r.caso_id for r in resultado.resultados if r.caso_id)),
    }


@enrutador.get("/panel/casos", response_class=HTMLResponse)
async def lista_casos(repo: RepoCasosDestinoDep, ajustes: AjustesDep) -> HTMLResponse:
    casos, _total = await casos_para_panel(repo, _limite(ajustes))
    return _render("paginas/casos.html", casos=casos)


@enrutador.get("/panel/casos/{cuenta}", response_class=HTMLResponse)
async def un_caso(cuenta: str, repo: RepoCasosDestinoDep) -> HTMLResponse:
    """El caso de una cuenta; vacio (sin elemento) si ya no tiene alertas abiertas."""
    caso = await repo.obtener(cuenta) if CUENTA.match(cuenta) else None
    if caso is None:
        return HTMLResponse("")
    return _render("paginas/caso.html", caso=con_sugerencia(caso), resumen=None)


@enrutador.post("/panel/lote", response_class=HTMLResponse)
async def lote(
    peticion: Request,
    ajustes: AjustesDep,
    repo: RepoAlertasDep,
    repo_casos: RepoCasosDestinoDep,
    sesion: SesionDep,
) -> HTMLResponse:
    """Transicion en lote desde la vista por casos. Devuelve el caso, ya actualizado."""
    cuerpo = parse_qs((await peticion.body()).decode("utf-8"), keep_blank_values=True)
    cuenta = cuerpo.get("cuenta", [""])[0]
    destino = cuerpo.get("destino", [""])[0]
    nota = cuerpo.get("nota", [""])[0].strip()
    ids = cuerpo.get("alerta", [])

    error: str | None = None
    resumen: dict[str, Any] | None = None
    estado_http = 200
    try:
        pedida = PeticionLote(
            alertas=ids,  # type: ignore[arg-type]
            destino=destino,  # type: ignore[arg-type]
            nota=f"[lote en {cuenta}] {nota}".strip() if cuenta else nota,
        )
        if len(set(pedida.alertas)) > ajustes.app.lote_maximo:
            raise ValueError(
                f"Se pueden procesar hasta {ajustes.app.lote_maximo} alertas por vez"
            )
        resultado = await aplicar_lote(pedida.alertas, pedida.destino, pedida.nota, repo, sesion)
        resumen = _resumen_lote(resultado, pedida.destino.value)
    except ValidationError:
        estado_http = 422
        error = "Selecciona al menos una alerta y una acción válida."
    except ValueError as fallo:
        estado_http = 422
        error = str(fallo)

    caso = await repo_casos.obtener(cuenta) if CUENTA.match(cuenta) else None
    html = ENTORNO.get_template("paginas/caso.html").render(
        caso=con_sugerencia(caso) if caso else None,
        resumen=resumen,
        error=error,
        cuenta=cuenta,
    )
    return HTMLResponse(html, status_code=estado_http)
