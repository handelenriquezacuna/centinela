"""El andamio de la SPA (T-61): versiones exactas, Node fijado y el CI en el orden correcto.

Lo mismo que `uv.lock` para Python: un `^` en `package.json` deja que dos maquinas
instalen cosas distintas con el mismo commit, y el dia de la defensa no es el dia de
descubrirlo. No necesita Node ni red: lee los archivos.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
FRONTEND = RAIZ / "frontend"
PAQUETE = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
CI = yaml.safe_load((RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
PASOS = CI["jobs"]["pruebas"]["steps"]
VERSION_EXACTA = re.compile(r"^\d+\.\d+\.\d+$")


def prueba_las_dependencias_van_en_version_exacta() -> None:
    sueltas = {
        nombre: version
        for seccion in ("dependencies", "devDependencies")
        for nombre, version in PAQUETE.get(seccion, {}).items()
        if not VERSION_EXACTA.match(version)
    }
    assert not sueltas, f"versiones no exactas en frontend/package.json: {sueltas}"


def prueba_el_lock_se_versiona_y_coincide_con_el_paquete() -> None:
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    raiz = lock["packages"][""]
    assert raiz["dependencies"] == PAQUETE["dependencies"]
    assert raiz["devDependencies"] == PAQUETE["devDependencies"]


def prueba_node_esta_fijado_igual_en_local_y_en_el_ci() -> None:
    nvmrc = (FRONTEND / ".nvmrc").read_text(encoding="utf-8").strip()
    assert VERSION_EXACTA.match(nvmrc), nvmrc
    assert PAQUETE["engines"]["node"] == nvmrc
    npmrc = (FRONTEND / ".npmrc").read_text(encoding="utf-8")
    assert "save-exact=true" in npmrc and "engine-strict=true" in npmrc
    nodo = next(p for p in PASOS if str(p.get("uses", "")).startswith("actions/setup-node@"))
    assert nodo["with"]["node-version-file"] == "frontend/.nvmrc"


def prueba_toda_accion_del_ci_va_fijada_por_sha() -> None:
    for paso in PASOS:
        if "uses" in paso:
            assert re.match(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", paso["uses"]), paso["uses"]


def prueba_el_ci_corre_las_compuertas_del_frontend_y_construye_antes_de_las_e2e() -> None:
    def indice(comando: str) -> int:
        return next(i for i, p in enumerate(PASOS) if p.get("run", "").strip() == comando)

    for comando in ("npm ci", "npm run lint", "npm run typecheck", "npm test", "npm audit --audit-level=high"):
        assert PASOS[indice(comando)].get("working-directory") == "frontend", comando
    assert indice("npm ci") < indice("npm run build") < indice("uv run pytest -m e2e -rs")
    assert CI["permissions"] == {"contents": "read"}


def prueba_el_build_sale_donde_fastapi_lo_busca_y_no_se_versiona() -> None:
    from app.api.rutas.spa import RUTA_SPA

    vite = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
    assert 'outDir: "../app/estaticos/spa"' in vite
    assert 'base: command === "build" ? "/estaticos/spa/" : "/"' in vite
    assert RUTA_SPA == RAIZ / "app" / "estaticos" / "spa"
    assert "app/estaticos/spa/" in (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
