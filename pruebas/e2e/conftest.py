"""Fixtures de las pruebas extremo a extremo (T-58, D-51).

Un navegador real (Chromium, via pytest-playwright) contra un `uvicorn` real, con la app
leyendo de una base propia, `antifraude_pruebas_e2e`, sembrada con los scripts reales y el
conjunto de demo. Nada de dobles: el recorrido es navegador -> HTTP -> app -> Mongo y de
regreso, con D1 y el canal SSE vivos dentro del proceso del servidor.

Orden de la sesion, y no otro:

1. Se siembra la base (01, 02, 03 y 05) y se carga el demo y dos agentes con credencial.
   Todo ANTES de arrancar el servidor: D1 empieza desde "ahora" y no debe procesar el demo.
2. Se arranca `uvicorn` en un puerto libre, como subproceso, con la URI y la base de la
   prueba por variables de entorno (la unica forma de moverlas: `app/nucleo/config.py`).
   Es `servidor_e2e`, con la interfaz por omision: la SPA (T-67). Necesita el build
   (`python tareas.py front`). Las e2e del respaldo HTMX viven en `htmx/` y van contra
   `servidor_htmx`, otro `uvicorn` sobre la misma base con `portal.interfaz: htmx`,
   mientras HTMX exista (lo retira T-68).
3. Se espera a que D1 haya abierto su change stream (aparece su token en
   `deteccion_estado`); una insercion anterior se perderia.

Entre pruebas se restaura lo que una prueba pudo mover (la alerta ALR-002 y lo que D1
creo), sin tocar a los agentes ni sus credenciales.

Trazas: Playwright graba cada prueba y las guarda en `pruebas/e2e/trazas/` SOLO si la
prueba fallo. Se abren con `uv run playwright show-trace <archivo>.zip`.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import BrowserContext, Page

from app.deteccion.d1 import NOMBRE_D1
from app.modelos import Agente, RolAgente
from app.nucleo.config import Ajustes
from app.nucleo.db import ciclo_cliente
from app.repos.agentes import RepoAgentes
from app.repos.carga_demo import cargar_demo
from app.seguridad.usuarios import asignar_credencial
from pruebas.conftest import RUTA_RAIZ, saltar_o_fallar
from pruebas.dobles.datos_demo import ALERTAS, DATOS_DEMO
from pruebas.sesiones import CLAVE_DE_PRUEBAS

BASE_E2E = "antifraude_pruebas_e2e"
TRAZAS = Path(__file__).resolve().parent / "trazas"

# Agentes de la prueba. Son nuevos (AGT-9xx): no chocan con los del demo y sus
# credenciales nunca se borran al restaurar.
CONTRASENA = "clave-e2e-0123456789"
AGENTE = "AGT-901"
SUPERVISOR = "AGT-902"

_JS_SEMILLA = """
const destino = db.getMongo().getDB("%(base)s");
destino.dropDatabase();
db.getSiblingDB = function () { return destino; };
destino.getSiblingDB = function () { return destino; };
print = function () {};
load("%(semilla)s/01-colecciones.js");
load("%(semilla)s/02-indices.js");
var BASE_CENTINELA = "%(base)s";
load("%(semilla)s/03-catalogos.js");
load("%(semilla)s/05-vistas.js");
"""

def con_base[T](operacion: Callable[[Any], Awaitable[T]], uri: str | None = None) -> T:
    """Corre `operacion(base)` contra la base de la prueba y devuelve su resultado.

    Playwright sync mantiene un bucle de eventos en el hilo de la prueba, y `asyncio.run`
    no se puede llamar ahi: la operacion corre en un hilo propio, con su propio bucle y su
    propio cliente (se cierra al terminar).
    """
    destino = uri or _uri()

    async def _correr() -> T:
        ajustes = Ajustes(mongo={"uri": destino, "base": BASE_E2E})
        async with ciclo_cliente(ajustes) as cliente:
            return await operacion(cliente[BASE_E2E])

    with ThreadPoolExecutor(max_workers=1) as hilo:
        return hilo.submit(asyncio.run, _correr()).result()


def _uri() -> str:
    """La URI del entorno (CI y local la ponen); si no, la del YAML del repo."""
    return Ajustes().mongo.uri


def _puerto_libre() -> int:
    with socket.socket() as sonda:
        sonda.bind(("127.0.0.1", 0))
        return int(sonda.getsockname()[1])


def _sembrar(uri: str) -> None:
    mongosh = shutil.which("mongosh")
    if mongosh is None:
        saltar_o_fallar("mongosh no esta en el PATH; hace falta para sembrar la base e2e")
    js = _JS_SEMILLA % {"base": BASE_E2E, "semilla": str(RUTA_RAIZ / "scripts" / "semilla")}
    resultado = subprocess.run(
        [mongosh, uri, "--quiet", "--eval", js],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if resultado.returncode != 0:
        saltar_o_fallar(f"no se pudo sembrar {BASE_E2E}: {resultado.stdout}{resultado.stderr}")


async def _cargar_agentes_y_demo(base: Any) -> None:
    await cargar_demo(base, DATOS_DEMO)
    await cargar_demo(
        base,
        {
            "agentes": [
                Agente(_id=AGENTE, nombre="Agente E2E", rol=RolAgente.AGENTE),
                Agente(_id=SUPERVISOR, nombre="Supervisor E2E", rol=RolAgente.SUPERVISOR),
            ]
        },
    )
    repo = RepoAgentes(base)
    for agente_id in (AGENTE, SUPERVISOR):
        await asignar_credencial(repo, agente_id, CONTRASENA)


def _esperar_servidor(url: str, proceso: subprocess.Popen[bytes], segundos: int = 60) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if proceso.poll() is not None:
            raise RuntimeError(f"uvicorn termino con codigo {proceso.returncode}")
        try:
            with urllib.request.urlopen(f"{url}/salud", timeout=2):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    raise RuntimeError(f"uvicorn no respondio en {segundos} s")


async def _d1_abierto(base: Any) -> bool:
    return await base["deteccion_estado"].find_one({"_id": NOMBRE_D1}) is not None


def _esperar_d1(segundos: int = 30) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if con_base(_d1_abierto):
            return
        time.sleep(0.3)
    raise RuntimeError("D1 no abrio su change stream a tiempo")


@contextmanager
def _uvicorn(uri: str, *, interfaz: str) -> Iterator[str]:
    """Un `uvicorn` real en un puerto libre, con la base de la prueba y la interfaz pedida."""
    puerto = _puerto_libre()
    entorno = {
        **os.environ,
        "CENTINELA_MONGO__URI": uri,
        "CENTINELA_MONGO__BASE": BASE_E2E,
        "CENTINELA_SESION__CLAVE": CLAVE_DE_PRUEBAS,
        "CENTINELA_PORTAL__INTERFAZ": interfaz,
    }
    with tempfile.TemporaryFile() as registro:
        proceso = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", str(puerto), "--log-level", "warning",
            ],
            cwd=RUTA_RAIZ,
            env=entorno,
            stdout=registro,
            stderr=subprocess.STDOUT,
        )
        url = f"http://127.0.0.1:{puerto}"
        try:
            _esperar_servidor(url, proceso)
            _esperar_d1()
            yield url
        except Exception:
            registro.seek(0)
            sys.stderr.write(registro.read().decode("utf-8", "replace"))
            raise
        finally:
            proceso.terminate()
            try:
                proceso.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proceso.kill()


INDICE_SPA = RUTA_RAIZ / "app" / "estaticos" / "spa" / "index.html"


@pytest.fixture(scope="session")
def servidor_e2e() -> Iterator[str]:
    """URL base del `uvicorn` de la prueba: la SPA, con la base sembrada y D1 escuchando.

    Sin el build la SPA responde 503, asi que falta de build es un salto en local y una
    falla con `CENTINELA_EXIGIR_MONGO=1` (el CI construye antes de las e2e).
    """
    if not INDICE_SPA.is_file():
        saltar_o_fallar("falta el build de la SPA (app/estaticos/spa/index.html): python tareas.py front")
    uri = _uri()
    _sembrar(uri)
    con_base(_cargar_agentes_y_demo, uri)
    try:
        with _uvicorn(uri, interfaz="spa") as url:
            yield url
    finally:
        con_base(_borrar_base, uri)


@pytest.fixture(scope="session")
def servidor_htmx(servidor_e2e: str) -> Iterator[str]:
    """Otro `uvicorn` sobre la MISMA base, con `portal.interfaz: htmx` (respaldo, T-67).

    Existe mientras viva el respaldo HTMX (T-68 lo retira): las e2e de `htmx/` van contra
    este. Los dos procesos corren D1 sobre la misma base; el indice unico de
    `alertas.transaccion_id` hace que una transaccion siga dando UNA alerta (lo mismo que
    prueba la resiliencia de T-52). El canal SSE de este servidor ve las alertas que
    escriba cualquiera de los dos: lee el change stream, no un bus en memoria.
    """
    with _uvicorn(_uri(), interfaz="htmx") as url:
        yield url


async def _borrar_base(base: Any) -> None:
    await base.client.drop_database(BASE_E2E)


async def _restaurar(base: Any) -> None:
    """Deja la base como la dejo la sesion: ALR-002 original y nada que D1 haya creado."""
    demo = {a.alerta_id for a in ALERTAS}
    segunda = next(a for a in ALERTAS if a.alerta_id == "ALR-002")
    from app.repos.carga_demo import a_entero_64

    await base["alertas"].replace_one(
        {"_id": segunda.alerta_id}, a_entero_64(segunda.a_documento())
    )
    await base["alertas"].delete_many({"_id": {"$nin": sorted(demo)}})
    ids_demo = [t.transaccion_id for t in DATOS_DEMO["transacciones"]]
    await base["transacciones"].delete_many({"_id": {"$nin": ids_demo}})
    # Las listas de riesgo: una prueba pudo quitar o agregar entradas (T-55).
    listas = DATOS_DEMO["listas_riesgo"]
    await base["listas_riesgo"].delete_many({"_id": {"$nin": [e.riesgo_id for e in listas]}})
    for entrada in listas:
        await base["listas_riesgo"].replace_one(
            {"_id": entrada.riesgo_id}, a_entero_64(entrada.a_documento()), upsert=True
        )
    # Los casos que abrio una escalacion en lote (T-56); los del demo se quedan.
    casos_demo = [c.caso_id for c in DATOS_DEMO["casos"]]
    await base["casos"].delete_many({"_id": {"$nin": casos_demo}})


@pytest.fixture(autouse=True)
def base_restaurada(servidor_e2e: str) -> None:
    con_base(_restaurar)


@pytest.fixture(scope="session")
def base_url(servidor_e2e: str) -> str:
    """pytest-playwright resuelve `page.goto("/ruta")` contra esta URL: la SPA."""
    return servidor_e2e


@pytest.fixture(scope="session")
def browser_context_args(
    browser_context_args: dict[str, Any], servidor_e2e: str
) -> dict[str, Any]:
    """Los contextos de este nivel van a la SPA, explicito (`htmx/` lo cambia por el suyo)."""
    return {**browser_context_args, "base_url": servidor_e2e}


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    resultado = yield
    reporte = resultado.get_result()
    setattr(item, f"reporte_{reporte.when}", reporte)


@pytest.fixture(autouse=True)
def traza_si_falla(
    context: BrowserContext, request: pytest.FixtureRequest
) -> Iterator[None]:
    """Graba la prueba y guarda la traza si fallo, o siempre con CENTINELA_E2E_TRAZA=siempre.

    La traza se abre con `uv run playwright show-trace pruebas/e2e/trazas/<prueba>.zip`:
    cada paso con captura, el DOM, la red y la consola. Es la forma de revisar un recorrido
    que paso, no solo uno que fallo.
    """
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    yield
    reporte = getattr(request.node, "reporte_call", None)
    siempre = os.environ.get("CENTINELA_E2E_TRAZA") == "siempre"
    if reporte is not None and (reporte.failed or siempre):
        TRAZAS.mkdir(parents=True, exist_ok=True)
        nombre = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)
        context.tracing.stop(path=str(TRAZAS / f"{nombre}.zip"))
    else:
        context.tracing.stop()


def iniciar_sesion(page: Page, agente_id: str, contrasena: str = CONTRASENA) -> None:
    """Entra por el formulario real, como lo hace una persona."""
    page.goto("/login")
    page.get_by_label("Identificador").fill(agente_id)
    page.get_by_label(re.compile("Contrase")).fill(contrasena)
    page.get_by_role("button", name="Entrar").click()
