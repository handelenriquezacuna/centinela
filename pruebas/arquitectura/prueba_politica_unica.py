"""T-19: una sola lectura de `politica_deteccion` en todo `app/`.

Antes habia dos (`app/repos/reglas.py` devolvia un dict y `app/repos/deteccion.py` el
modelo), y dos lecturas de la misma politica terminan separandose: un filtro distinto,
una validacion distinta. La lectura es `leer_politica_vigente` en `app/repos/reglas.py`.

Se verifica sobre el arbol de sintaxis: solo ese modulo puede indexar la base con
`COLECCION_POLITICA` o con el texto "politica_deteccion".
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
APP = RAIZ / "app"
UNICO = APP / "repos" / "reglas.py"


def _accesos_a_la_politica(ruta: Path) -> list[int]:
    lineas = []
    for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
        if not isinstance(nodo, ast.Subscript):
            continue
        indice = nodo.slice
        if (isinstance(indice, ast.Name) and indice.id == "COLECCION_POLITICA") or (
            isinstance(indice, ast.Constant) and indice.value == "politica_deteccion"
        ):
            lineas.append(nodo.lineno)
    return lineas


def prueba_politica_deteccion_se_lee_en_un_solo_lugar() -> None:
    fuera = {
        str(ruta.relative_to(RAIZ)): lineas
        for ruta in sorted(APP.rglob("*.py"))
        if ruta != UNICO and (lineas := _accesos_a_la_politica(ruta))
    }
    assert fuera == {}, f"politica_deteccion leida fuera de {UNICO.name}: {fuera}"
    assert _accesos_a_la_politica(UNICO), "la lectura unica tiene que existir"


def prueba_los_dos_repos_usan_la_misma_funcion() -> None:
    from app.repos import deteccion, reglas

    assert deteccion.leer_politica_vigente is reglas.leer_politica_vigente
