"""H-11 · Pagina de notificaciones del respaldo HTMX. Fuera de `/api/v1`.

Es la misma lectura que `GET /api/v1/notificaciones` (`elemento_de`), dibujada en el
servidor. Existe mientras viva el respaldo HTMX (lo retira T-68): las dos interfaces
publican las mismas pantallas. Con `portal.interfaz: spa` la ruta la sirve la SPA.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.dependencias import AjustesDep, RepoNotificacionesDep
from app.api.paginacion import acotar_limite
from app.api.rutas.notificaciones import elemento_de
from app.api.rutas.paginas import ENTORNO, NAVEGACION

enrutador = APIRouter(include_in_schema=False)


@enrutador.get("/notificaciones", response_class=HTMLResponse)
async def pagina_notificaciones(
    ajustes: AjustesDep, repo: RepoNotificacionesDep
) -> HTMLResponse:
    limite = acotar_limite(
        None,
        maximo=ajustes.app.pagina_maxima,
        por_omision=ajustes.app.pagina_por_omision,
    )
    documentos, total = await repo.listar(estado_envio=None, limite=limite, desplazamiento=0)
    html = ENTORNO.get_template("paginas/notificaciones.html").render(
        navegacion=NAVEGACION,
        activa="notificaciones",
        elementos=[elemento_de(documento) for documento in documentos],
        total=total,
    )
    return HTMLResponse(html)
