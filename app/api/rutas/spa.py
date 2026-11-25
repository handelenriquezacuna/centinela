"""Las rutas de pagina cuando `portal.interfaz` es `spa` (T-61, D-60 a D-63).

La SPA React se construye con `python tareas.py front` (`npm run build` en `frontend/`) y
queda en `app/estaticos/spa/` (ignorada por git). Su JavaScript y su CSS salen del montaje
`/estaticos` de siempre; aqui solo se devuelve `index.html` en cada ruta de pagina, para
que React Router dibuje la pantalla que corresponde a la URL. Recargar `/detalle/ALR-001`
funciona porque el servidor responde esa ruta con la misma pagina.

Las rutas son exactamente las de pagina del portal HTMX, con el mismo metodo y la misma
plantilla: la tabla de permisos no cambia de una interfaz a la otra. El guardia corre
antes que estas funciones, asi que sin sesion la pagina redirige a `/login?siguiente=...`
igual que hoy, y con un rol sin permiso devuelve el `index.html` con estado 403 (la SPA
muestra su pantalla de acceso denegado; ver `app/api/sesion.py`).

`spa` es la interfaz por omision desde T-67. Con `htmx` (el respaldo hasta T-68) este
router no se registra y nada de aqui corre.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, status
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.modelos.alertas import RolAgente

RUTA_SPA = Path(__file__).resolve().parents[2] / "estaticos" / "spa"

# Las paginas de la SPA. Las mismas plantillas que declara la tabla de permisos.
PAGINAS_SPA: tuple[str, ...] = (
    "/",
    "/login",
    "/panel",
    "/detalle",
    "/detalle/{alerta_id}",
    "/busqueda",
    "/red-mulas",
    "/red-mulas/{cuenta}",
    "/tablero",
    "/reglas",
    "/administracion",
    "/administracion/salud",
    "/administracion/respaldos",
    "/administracion/usuarios",
)

# El menu de la SPA: (ruta, rotulo, area). El orden es el de trabajo. Cada rol ve solo
# lo que su permiso abre (`GET /api/v1/sesion` filtra con la tabla de permisos).
NAVEGACION_SPA: tuple[tuple[str, str, str], ...] = (
    ("/panel", "Panel", "Alertas"),
    ("/busqueda", "Búsqueda", "Alertas"),
    ("/red-mulas", "Red de mulas", "Alertas"),
    ("/tablero", "Tablero", "Supervisión"),
    ("/reglas", "Reglas y política", "Supervisión"),
    ("/administracion/salud", "Salud del sistema", "Administración"),
    ("/administracion/respaldos", "Respaldos", "Administración"),
    ("/administracion/usuarios", "Usuarios del portal", "Administración"),
)

# Donde aterriza cada rol al entrar: la agente en su cola, el supervisor en lo pendiente,
# el administrador en la salud del sistema.
INICIO_POR_ROL: dict[RolAgente, str] = {
    RolAgente.AGENTE: "/panel",
    RolAgente.SUPERVISOR: "/tablero",
    RolAgente.ADMINISTRADOR: "/administracion/salud",
}

SIN_BUILD = (
    "<!doctype html><html lang=\"es\"><meta charset=\"utf-8\"><title>Centinela</title>"
    "<main><h1>Falta construir la interfaz</h1><p>portal.interfaz es <code>spa</code> "
    "pero no existe <code>app/estaticos/spa/index.html</code>. Correr "
    "<code>python tareas.py front</code> y recargar, o arrancar con "
    "<code>CENTINELA_PORTAL__INTERFAZ=htmx</code> para usar el respaldo.</p></main></html>"
)

enrutador = APIRouter(include_in_schema=False)


def respuesta_spa(estado: int = status.HTTP_200_OK) -> Response:
    """`index.html` de la SPA; sin build, las instrucciones para construirla.

    Sin build una pagina normal responde 503; un 403 sigue siendo 403 (con las mismas
    instrucciones): una negacion de permiso nunca debe parecer una caida.
    `no-cache`: el index nombra los archivos con hash del build; si el navegador lo
    guardara, despues de un build nuevo pediria archivos que ya no existen.
    """
    indice = RUTA_SPA / "index.html"
    if not indice.is_file():
        sin_build = status.HTTP_503_SERVICE_UNAVAILABLE if estado < 400 else estado
        return HTMLResponse(SIN_BUILD, status_code=sin_build)
    return FileResponse(
        indice,
        status_code=estado,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


async def pagina_spa() -> Response:
    return respuesta_spa()


for _ruta in PAGINAS_SPA:
    enrutador.add_api_route(_ruta, pagina_spa, methods=["GET"], name=f"spa:{_ruta}")
