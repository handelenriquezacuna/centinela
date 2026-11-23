"""La tabla unica de permisos: que rol puede hacer que, y que ruta exige que.

Dos tablas y nada mas, las dos aqui:

- `PERMISOS_POR_ROL`: que permisos tiene cada rol.
- `ACCESOS`: que permiso exige cada ruta (metodo + plantilla de ruta). `None` es publica.

El guardia de `app/api/sesion.py` consulta `ACCESOS` para toda peticion que llega a una
ruta, de modo que una ruta nueva sin entrada aqui responde 403 (falla cerrada) y la
prueba de contrato la senala. Las pruebas generan "cada ruta contra cada rol" de estas
mismas tablas, asi que no hay una segunda lista que mantener.

Sin sesion solo se puede entrar a `/login`, `/salud` y los estaticos (el montaje
`/estaticos` no pasa por rutas de FastAPI y es publico por construccion).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from starlette.routing import compile_path

from app.modelos.alertas import RolAgente


class Permiso(StrEnum):
    SESION = "sesion"  # basta con haber iniciado sesion, sea cual sea el rol
    PANEL = "panel"
    DETALLE = "detalle"
    RESOLVER = "resolver"
    BUSCAR = "buscar"
    RED_MULAS = "red_mulas"
    TABLERO = "tablero"
    VER_REGLAS = "ver_reglas"
    EDITAR_REGLAS = "editar_reglas"
    GESTIONAR_LISTAS = "gestionar_listas"  # aprobar propuestas de la red y dar de baja


_PERMISOS_AGENTE = frozenset(
    {
        Permiso.SESION,
        Permiso.PANEL,
        Permiso.DETALLE,
        Permiso.RESOLVER,
        Permiso.BUSCAR,
        Permiso.RED_MULAS,
    }
)

PERMISOS_POR_ROL: dict[RolAgente, frozenset[Permiso]] = {
    RolAgente.AGENTE: _PERMISOS_AGENTE,
    RolAgente.SUPERVISOR: _PERMISOS_AGENTE
    | {Permiso.TABLERO, Permiso.VER_REGLAS, Permiso.EDITAR_REGLAS, Permiso.GESTIONAR_LISTAS},
    RolAgente.ADMINISTRADOR: frozenset(Permiso),
}


def rol_tiene(rol: RolAgente, permiso: Permiso) -> bool:
    return permiso in PERMISOS_POR_ROL[rol]


@dataclass(frozen=True)
class Acceso:
    metodo: str
    ruta: str  # plantilla, como la declara la ruta: /detalle/{alerta_id}
    permiso: Permiso | None  # None = publica

    @property
    def patron(self) -> re.Pattern[str]:
        return compile_path(self.ruta)[0]

    @property
    def parametros(self) -> int:
        return self.ruta.count("{")


def _a(metodo: str, ruta: str, permiso: Permiso | None) -> Acceso:
    return Acceso(metodo, ruta, permiso)


ACCESOS: tuple[Acceso, ...] = (
    # Sin sesion
    _a("GET", "/", None),
    _a("GET", "/login", None),
    _a("POST", "/login", None),
    _a("POST", "/salir", None),
    _a("GET", "/salud", None),
    _a("POST", "/api/v1/sesion", None),
    # Cualquier sesion
    _a("GET", "/estado", Permiso.SESION),
    _a("GET", "/openapi.json", Permiso.SESION),
    # Panel y alertas
    _a("GET", "/panel", Permiso.PANEL),
    _a("GET", "/panel/cola", Permiso.PANEL),
    _a("GET", "/api/v1/alertas", Permiso.PANEL),
    _a("GET", "/api/v1/alertas/flujo", Permiso.PANEL),
    # Detalle y resolucion
    _a("GET", "/detalle", Permiso.DETALLE),
    _a("GET", "/detalle/{alerta_id}", Permiso.DETALLE),
    _a("POST", "/detalle/{alerta_id}/transicion", Permiso.RESOLVER),
    _a("POST", "/api/v1/alertas/{alerta_id}/transiciones", Permiso.RESOLVER),
    # Busqueda forense
    _a("GET", "/busqueda", Permiso.BUSCAR),
    _a("GET", "/busqueda/exportar.csv", Permiso.BUSCAR),
    _a("GET", "/api/v1/transacciones", Permiso.BUSCAR),
    # Red de mulas
    _a("GET", "/red-mulas", Permiso.RED_MULAS),
    _a("GET", "/red-mulas/{cuenta}", Permiso.RED_MULAS),
    _a("GET", "/api/v1/red-mulas/{cuenta}", Permiso.RED_MULAS),
    # Listas de riesgo: solo supervisor y administrador
    _a("POST", "/api/v1/red-mulas/{cuenta}/propuestas/{propuesta}", Permiso.GESTIONAR_LISTAS),
    _a("POST", "/red-mulas/{cuenta}/propuestas/{propuesta}", Permiso.GESTIONAR_LISTAS),
    _a("DELETE", "/api/v1/listas-riesgo/{riesgo_id}", Permiso.GESTIONAR_LISTAS),
    _a("POST", "/red-mulas/{cuenta}/listas-riesgo/{riesgo_id}/baja", Permiso.GESTIONAR_LISTAS),
    # Tablero
    _a("GET", "/tablero", Permiso.TABLERO),
    _a("GET", "/api/v1/efectividad", Permiso.TABLERO),
    # Reglas y politica
    _a("GET", "/reglas", Permiso.VER_REGLAS),
    _a("GET", "/api/v1/reglas", Permiso.VER_REGLAS),
    _a("GET", "/api/v1/reglas/tipos", Permiso.VER_REGLAS),
    _a("GET", "/api/v1/reglas/{regla_id}", Permiso.VER_REGLAS),
    _a("GET", "/api/v1/politica", Permiso.VER_REGLAS),
    _a("POST", "/reglas/{regla_id}", Permiso.EDITAR_REGLAS),
    _a("POST", "/api/v1/reglas", Permiso.EDITAR_REGLAS),
    _a("POST", "/api/v1/reglas/simular", Permiso.EDITAR_REGLAS),
    _a("PATCH", "/api/v1/reglas/{regla_id}", Permiso.EDITAR_REGLAS),
    _a("PATCH", "/api/v1/politica", Permiso.EDITAR_REGLAS),
)

_PREFIJOS_API = ("/api/", "/estado", "/openapi.json", "/salud")


def resolver_acceso(metodo: str, ruta: str) -> Acceso | None:
    """La entrada de `ACCESOS` que corresponde a una peticion concreta, si hay."""
    candidatos = [
        acceso
        for acceso in ACCESOS
        if acceso.metodo == metodo.upper() and acceso.patron.match(ruta)
    ]
    # La mas especifica gana: /api/v1/reglas/tipos antes que /api/v1/reglas/{regla_id}.
    return min(candidatos, key=lambda a: a.parametros, default=None)


def es_superficie_api(ruta: str) -> bool:
    """True si el cliente espera JSON; False si es una pagina del portal."""
    return ruta.startswith(_PREFIJOS_API)


def permite(rol: RolAgente | None, metodo: str, ruta: str) -> bool:
    """Si un rol puede hacer esa peticion. Sin rol, solo lo publico."""
    acceso = resolver_acceso(metodo, ruta)
    if acceso is None:
        return False
    if acceso.permiso is None:
        return True
    return rol is not None and rol_tiene(rol, acceso.permiso)
