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
VENDORIZADOS = ("htmx-ext-sse.js",)

# Dominios de los que NO se carga nada. Si aparece uno en `app/`, alguien cambio un
# archivo local por una etiqueta con `src` a internet.
CDN_PROHIBIDOS = ("unpkg.com", "jsdelivr.net", "cdnjs.cloudflare.com", "cdn.")

# Extensiones que se revisan buscando referencias a CDN. La procedencia se documenta en
# Markdown y ahi si es legitimo nombrar de donde salio el archivo.
EXTENSIONES_REVISADAS = (".py", ".js", ".css", ".html")


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
    """Un archivo de una linea pasa el chequeo de tamaño y no sirve para nada.

    El umbral no es una medida de calidad: es la diferencia entre la biblioteca y un
    `TODO: bajar el archivo`.
    """
    contenido = (ESTATICOS / nombre).read_text(encoding="utf-8")
    lineas = [linea for linea in contenido.splitlines() if linea.strip()]
    assert len(lineas) > 20, (
        f"{nombre} tiene {len(lineas)} lineas con contenido: parece un marcador, "
        "no la biblioteca"
    )


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


def prueba_nada_de_app_carga_desde_un_cdn() -> None:
    culpables: list[str] = []
    for archivo in sorted((RAIZ / "app").rglob("*")):
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
