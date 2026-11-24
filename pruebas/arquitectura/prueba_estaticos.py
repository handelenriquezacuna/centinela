"""Los estaticos vendorizados: que esten, que sean lo que dicen, y que nada venga de un CDN.

`app/estaticos/PROCEDENCIA.md` dice que al reemplazar un archivo vendorizado se corre
`tareas.py pruebas --tipo arquitectura` y que eso verifica el archivo. Este modulo es lo
que hace cierta esa frase: antes la documentacion prometia una compuerta que no existia,
y un doc que promete una compuerta ausente es peor que no prometerla -alguien reemplaza el
archivo por uno vacio, corre las pruebas, ve verde y se va tranquilo-.

La regla del CDN no es estetica. El dia de la defensa puede no haber internet, y una
pantalla que se queda sin su biblioteca porque no cargo de unpkg es un error en tiempo de
ejecucion delante del profesor. Por eso todo lo que el navegador necesita vive en el repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
ESTATICOS = RAIZ / "app" / "estaticos"

# Lo que el navegador necesita y por eso vive en el repo. La extension es parte del
# contrato: un `.js` que llego como `.txt` no lo sirve el navegador como script.
VENDORIZADOS = ("htmx-ext-sse.js", "htmx.min.js")

# Dominios de los que NO se carga nada. Si aparece uno en `app/`, alguien cambio un
# archivo local por una etiqueta con `src` a internet.
CDN_PROHIBIDOS = ("unpkg.com", "jsdelivr.net", "cdnjs.cloudflare.com", "cdn.")

# Extensiones que se revisan buscando referencias a CDN. La procedencia se documenta en
# Markdown y ahi si es legitimo nombrar de donde salio el archivo.
EXTENSIONES_REVISADAS = (".py", ".js", ".css", ".html", ".ts", ".tsx")

# El build de la SPA (T-61) es generado e ignorado por git: la regla se aplica a su
# fuente, `frontend/`, que es lo que alguien edita. Sus dependencias se instalan con
# `npm ci` desde el lock y quedan dentro del bundle; ninguna se carga de internet.
SPA_GENERADA = ESTATICOS / "spa"
FUENTE_SPA = (RAIZ / "frontend" / "src", RAIZ / "frontend" / "index.html")


def prueba_la_carpeta_de_estaticos_existe() -> None:
    assert ESTATICOS.is_dir(), f"falta la carpeta {ESTATICOS}"


@pytest.mark.parametrize("nombre", VENDORIZADOS)
def prueba_el_vendorizado_existe_y_no_esta_vacio(nombre: str) -> None:
    archivo = ESTATICOS / nombre
    assert archivo.is_file(), (
        f"falta {nombre} en app/estaticos/. Se vendoriza a proposito: ver PROCEDENCIA.md"
    )
    assert archivo.stat().st_size > 0, f"{nombre} esta vacio"


@pytest.mark.parametrize("nombre", VENDORIZADOS)
def prueba_el_vendorizado_tiene_contenido_real_y_no_un_marcador(nombre: str) -> None:
    """Un marcador (`TODO: bajar el archivo`) pesa unos bytes; la biblioteca, varios KB.

    Se mide en bytes y no en lineas: un `.min.js` legitimo ocupa una sola linea.
    """
    tamano = (ESTATICOS / nombre).stat().st_size
    assert tamano > 2_000, (
        f"{nombre} pesa {tamano} bytes: parece un marcador, no la biblioteca"
    )


@pytest.mark.parametrize("nombre", VENDORIZADOS)
def prueba_el_vendorizado_coincide_con_el_hash_de_su_procedencia(nombre: str) -> None:
    """El archivo servido es exactamente el que PROCEDENCIA.md dice que se bajo."""
    import hashlib
    import re

    procedencia = (ESTATICOS / "PROCEDENCIA.md").read_text(encoding="utf-8")
    fila = next(
        (linea for linea in procedencia.splitlines() if f"`{nombre}`" in linea), None
    )
    assert fila is not None, f"{nombre} no tiene fila en PROCEDENCIA.md"
    esperado = re.findall(r"`([0-9a-f]{64})`", fila)
    assert esperado, f"la fila de {nombre} en PROCEDENCIA.md no trae SHA-256"
    real = hashlib.sha256((ESTATICOS / nombre).read_bytes()).hexdigest()
    assert real == esperado[0], f"{nombre} no coincide con el hash de PROCEDENCIA.md"


@pytest.mark.parametrize("nombre", VENDORIZADOS)
def prueba_la_extension_es_la_que_el_navegador_espera(nombre: str) -> None:
    assert (ESTATICOS / nombre).suffix == ".js", (
        f"{nombre} no es .js; el navegador no lo va a servir como script"
    )


def prueba_la_procedencia_esta_documentada() -> None:
    """De donde salio cada archivo y con que version, para poder actualizarlo despues."""
    procedencia = ESTATICOS / "PROCEDENCIA.md"
    assert procedencia.is_file(), "falta app/estaticos/PROCEDENCIA.md"
    texto = procedencia.read_text(encoding="utf-8")
    for nombre in VENDORIZADOS:
        assert nombre in texto, f"PROCEDENCIA.md no menciona {nombre}"


def _revisados() -> list[Path]:
    archivos = [a for a in (RAIZ / "app").rglob("*") if SPA_GENERADA not in a.parents]
    for fuente in FUENTE_SPA:
        archivos += [fuente] if fuente.is_file() else list(fuente.rglob("*"))
    return sorted(archivos)


def prueba_la_fuente_de_la_spa_se_revisa() -> None:
    """Contraprueba: la regla del CDN no pasa en verde por no mirar `frontend/`."""
    revisados = {a.relative_to(RAIZ).as_posix() for a in _revisados()}
    assert "frontend/index.html" in revisados
    assert "frontend/src/main.tsx" in revisados


def prueba_nada_de_app_carga_desde_un_cdn() -> None:
    culpables: list[str] = []
    for archivo in _revisados():
        if not archivo.is_file() or archivo.suffix not in EXTENSIONES_REVISADAS:
            continue
        texto = archivo.read_text(encoding="utf-8", errors="ignore").lower()
        for dominio in CDN_PROHIBIDOS:
            if dominio in texto:
                culpables.append(f"{archivo.relative_to(RAIZ)} menciona {dominio}")
    assert not culpables, (
        "todo lo que el navegador necesita se vendoriza; el dia de la defensa puede "
        "no haber internet:\n  " + "\n  ".join(culpables)
    )
