#!/usr/bin/env python3
"""Lanzador de tareas de Centinela.

Solo biblioteca estandar, a proposito: `argparse` y `subprocess`. No es make, no es
just, no es un script de shell. La razon es concreta: el equipo trabaja en macOS y en
Windows, y make no viene en Windows mientras que `python tareas.py` corre igual en los
dos. Un archivo .sh habria excluido a la mitad del equipo.

Uso:

    python tareas.py arriba          levanta el replica set y lo inicializa
    python tareas.py abajo           apaga el replica set
    python tareas.py estado          muestra el estado del replica set
    python tareas.py instalar        sincroniza dependencias con uv
    python tareas.py dev             corre la API en el host, con recarga
    python tareas.py datos-demo      carga el conjunto de demo
    python tareas.py pruebas         corre las pruebas
    python tareas.py verificar       instala, prueba y reporta

Con `--dry-run` cualquier tarea imprime lo que haria sin hacerlo.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
COMPOSE = RAIZ / "infra" / "docker-compose.yml"

# El replica set y su script de inicializacion ya existian antes de H-00A. Este
# lanzador los reusa, no los redefine.
SCRIPT_INIT = RAIZ / "scripts" / "init-replicaset.js"

# La siembra de estructura y catalogos, EN ESTE ORDEN y no en otro: 01 hace drop de
# las colecciones para recrearlas con su validador, asi que correrlo despues del 03 se
# lleva el catalogo de reglas que el 03 acaba de escribir. Es un error que ya ocurrio
# en vivo, y el orden es la unica proteccion.
SCRIPTS_SEMILLA = (
    "01-colecciones.js",  # colecciones y validadores $jsonSchema (H-01)
    "02-indices.js",      # indices con nombre (H-06)
    "03-catalogos.js",    # reglas de deteccion y politica de severidad (H-07)
)


class Fallo(Exception):
    """Error esperado de una tarea. Se reporta sin rastro de pila."""


def _buscar(programa: str) -> str:
    ruta = shutil.which(programa)
    if ruta is None:
        raise Fallo(
            f"no se encontro '{programa}' en el PATH.\n"
            f"  uv:     https://docs.astral.sh/uv/getting-started/installation/\n"
            f"  docker: Docker Desktop"
        )
    return ruta


def correr(orden: list[str], *, dry_run: bool = False, permitir_fallo: bool = False) -> int:
    """Ejecuta un comando mostrandolo antes. Sin shell: los argumentos van tal cual."""
    print("$ " + " ".join(orden))
    if dry_run:
        return 0

    resultado = subprocess.run(orden, cwd=RAIZ)
    if resultado.returncode != 0 and not permitir_fallo:
        raise Fallo(f"el comando termino con codigo {resultado.returncode}")
    return resultado.returncode


def compose(*argumentos: str) -> list[str]:
    return [_buscar("docker"), "compose", "-f", str(COMPOSE), *argumentos]


def uv(*argumentos: str) -> list[str]:
    return [_buscar("uv"), *argumentos]


def uri_mongo() -> str:
    """URI del replica set, leida de la configuracion y no escrita aqui.

    `app/nucleo/config.py` es el unico lugar que lee el YAML, asi que el lanzador se
    la pregunta en vez de duplicarla. `uv run` sincroniza el entorno solo si hace
    falta, asi que esto funciona tambien en un checkout limpio.
    """
    uri = _salida(
        uv(
            "run",
            "python",
            "-c",
            "from app.nucleo.config import obtener_ajustes;"
            "print(obtener_ajustes().mongo.uri)",
        )
    )
    if not uri:
        raise Fallo(
            "no se pudo leer mongo.uri de la configuracion.\n"
            "Probar primero: python tareas.py instalar"
        )
    return uri.splitlines()[-1].strip()


def uri_directa() -> str:
    """URI de conexion directa al primer nodo, sin descubrimiento de replica set.

    Hace falta exactamente para una cosa: `rs.initiate()`. Antes de iniciar el
    conjunto no hay replica set que descubrir, asi que una URI con
    `replicaSet=rsfraude` falla con `MongoServerSelectionError` y parece que el
    clúster no levanto. Para todo lo demas se usa la URI completa.

    El host sale de la URI configurada; no se escribe aqui.
    """
    from urllib.parse import urlsplit

    partes = urlsplit(uri_mongo())
    primer_nodo = partes.netloc.split(",")[0]
    return f"mongodb://{primer_nodo}/?directConnection=true"


def mongosh_directo(*argumentos: str) -> list[str]:
    """`mongosh` contra un solo nodo. Solo para inicializar el replica set."""
    return [_buscar("mongosh"), uri_directa(), "--quiet", *argumentos]


def mongosh(*argumentos: str) -> list[str]:
    """`mongosh` DESDE EL HOST, con la URI completa del replica set.

    Dos razones para no usar `docker compose exec`, y las dos ya nos costaron tiempo:

    1. `mongosh --port 27018` habla con un nodo suelto, y mongo1 no siempre es el
       primario: despues de una eleccion puede contestar "not primary". Con la URI
       completa, el driver descubre el primario solo.
    2. El bind mount de /scripts apunta a la carpeta del host de quien recreo los
       contenedores de ultimo. Con varios checkouts del repo, `--file /scripts/x.js`
       lee el archivo de otra persona. Corriendo desde el host con ruta absoluta del
       host, el archivo es siempre el propio.
    """
    return [_buscar("mongosh"), uri_mongo(), "--quiet", *argumentos]


def _salida(orden: list[str]) -> str:
    """Ejecuta y devuelve la salida. Cadena vacia si el comando falla."""
    try:
        resultado = subprocess.run(
            orden, cwd=RAIZ, capture_output=True, text=True, timeout=30
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return resultado.stdout.strip() if resultado.returncode == 0 else ""


def esperar_primario(segundos: int = 60, dry_run: bool = False) -> bool:
    """Espera a que la eleccion termine y haya un primario.

    Sin esta espera, `arriba` reporta tres SECONDARY y parece que algo salio mal: la
    eleccion tarda entre 5 y 15 segundos y el comando terminaba antes. Es la causa
    de la fila "el primario no aparece" de la tabla de problemas de
    docs/05-infraestructura.md.
    """
    if dry_run:
        return True

    consulta = 'rs.status().members.some(m => m.stateStr === "PRIMARY")'
    orden = mongosh("--eval", consulta)  # la URI se resuelve una sola vez
    limite = time.monotonic() + segundos

    while time.monotonic() < limite:
        if _salida(orden) == "true":
            return True
        time.sleep(2)

    return False


# ---------------------------------------------------------------------------
# Tareas
# ---------------------------------------------------------------------------


def tarea_arriba(args: argparse.Namespace) -> None:
    """Levanta los tres nodos e inicializa el replica set.

    Los dos pasos van juntos porque separarlos es la causa numero uno de
    `NotYetInitialized`: alguien levanta los contenedores, se olvida del segundo
    comando y pasa media hora buscando por que fallan los change streams.

    `init-replicaset.js` es idempotente: correrlo de nuevo no hace nada.
    """
    correr(compose("up", "-d", "--wait"), dry_run=args.dry_run)

    print("\nInicializando el replica set (idempotente)...")
    correr(mongosh_directo("--file", str(SCRIPT_INIT)), dry_run=args.dry_run)

    print("\nEsperando la eleccion del primario...")
    if not esperar_primario(dry_run=args.dry_run):
        raise Fallo(
            "no aparecio un primario. Ver el estado con: python tareas.py estado\n"
            "Si los contenedores se caen al arrancar, suele ser poca memoria "
            "asignada a Docker."
        )

    print("\nEstado:")
    tarea_estado(args)
    print(
        "\nListo. Siguiente paso:\n"
        "  python tareas.py datos-demo\n"
        "  python tareas.py dev"
    )


def tarea_abajo(args: argparse.Namespace) -> None:
    """Apaga los contenedores. Los datos sobreviven en los volumenes."""
    orden = compose("down")
    if args.borrar_datos:
        orden.append("-v")
        print("Se borran los volumenes: el replica set habra que inicializarlo de nuevo.")
    correr(orden, dry_run=args.dry_run)


def tarea_estado(args: argparse.Namespace) -> None:
    """Un primario y dos secundarios es lo que se espera ver."""
    correr(
        mongosh(
            "--eval",
            "rs.status().members.map(m => m.name + ' -> ' + m.stateStr).join('\\n')",
        ),
        dry_run=args.dry_run,
        permitir_fallo=True,
    )


def tarea_instalar(args: argparse.Namespace) -> None:
    """Crea el entorno con el interprete y las versiones exactas del lock."""
    correr(uv("sync"), dry_run=args.dry_run)


def tarea_dev(args: argparse.Namespace) -> None:
    """Modalidad de arranque A: Mongo en Compose, Python en el host con recarga.

    Un solo worker a proposito: la deteccion tiene que estar activa exactamente una
    vez. Dos workers escuchando el mismo change stream crean la alerta dos veces.
    """
    from app.nucleo.config import obtener_ajustes  # import tardio: no es stdlib

    ajustes = obtener_ajustes()
    orden = uv(
        "run",
        "uvicorn",
        "app.main:app",
        "--host",
        ajustes.servidor.host,
        "--port",
        str(ajustes.servidor.puerto),
    )
    if ajustes.servidor.recarga:
        orden.append("--reload")

    print(
        f"API en http://{ajustes.servidor.host}:{ajustes.servidor.puerto}\n"
        f"  documentacion: http://{ajustes.servidor.host}:{ajustes.servidor.puerto}/docs\n"
        f"  salud:         http://{ajustes.servidor.host}:{ajustes.servidor.puerto}/salud\n"
    )
    correr(orden, dry_run=args.dry_run)


def tarea_datos_demo(args: argparse.Namespace) -> None:
    """Siembra estructura y catalogos, y carga el conjunto de demo.

    Tres pasos, en orden:

    1. `scripts/semilla/01-colecciones.js` y `02-indices.js`: colecciones con sus
       validadores e indices.
    2. `scripts/semilla/03-catalogos.js`: el catalogo de reglas y la politica de
       severidad. El catalogo NO esta en el cargador de Python a proposito: definirlo
       en dos lugares es garantizar que se separen.
    3. Los documentos de dominio del caso documentado (Maria Rodriguez, TXN-001).

    Los scripts de semilla son de H-01, H-06 y H-07. Si todavia no estan en este
    checkout, el paso 1 y 2 se omiten con aviso y se carga solo el dominio.
    """
    directorio = RAIZ / "scripts" / "semilla"
    presentes = [directorio / nombre for nombre in SCRIPTS_SEMILLA]
    faltantes = [ruta.name for ruta in presentes if not ruta.is_file()]

    if faltantes:
        print(
            "AVISO: no estan en este checkout los scripts de semilla "
            f"{', '.join(faltantes)}.\n"
            "  Llegan con las ramas de H-01, H-06 y H-07. Se carga solo el dominio;\n"
            "  el catalogo de reglas y la politica de severidad quedan sin sembrar.\n"
        )
    else:
        for ruta in presentes:
            print(f"Sembrando {ruta.name}...")
            correr(mongosh("--file", str(ruta)), dry_run=args.dry_run)

    print("Cargando los documentos de dominio del demo...")
    correr(uv("run", "python", "-m", "pruebas.dobles.datos_demo"), dry_run=args.dry_run)


def tarea_pruebas(args: argparse.Namespace) -> None:
    """Corre las pruebas. Las que necesitan Mongo se saltan solas si no esta."""
    orden = uv("run", "pytest")

    if args.tipo != "todas":
        orden.append(f"pruebas/{args.tipo}")
    if args.sin_mongo:
        orden.extend(["-m", "not mongo"])
    if args.verboso:
        orden.append("-v")
    else:
        orden.append("-q")

    correr(orden, dry_run=args.dry_run)


def tarea_verificar(args: argparse.Namespace) -> None:
    """Lo que se corre antes de pedir revision."""
    tarea_instalar(args)
    tarea_pruebas(args)


TAREAS = {
    "arriba": tarea_arriba,
    "abajo": tarea_abajo,
    "estado": tarea_estado,
    "instalar": tarea_instalar,
    "dev": tarea_dev,
    "datos-demo": tarea_datos_demo,
    "pruebas": tarea_pruebas,
    "verificar": tarea_verificar,
}


def construir_analizador() -> argparse.ArgumentParser:
    analizador = argparse.ArgumentParser(
        prog="python tareas.py",
        description="Lanzador de tareas de Centinela.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analizador.add_argument(
        "--dry-run",
        action="store_true",
        help="imprime los comandos sin ejecutarlos",
    )

    subs = analizador.add_subparsers(dest="tarea", required=True, metavar="tarea")

    for nombre, funcion in TAREAS.items():
        documento = (funcion.__doc__ or "").strip().splitlines()[0]
        sub = subs.add_parser(nombre, help=documento, description=funcion.__doc__)
        sub.set_defaults(funcion=funcion)

        if nombre == "abajo":
            sub.add_argument(
                "--borrar-datos",
                action="store_true",
                help="borra tambien los volumenes (empezar de cero)",
            )
        if nombre in {"pruebas", "verificar"}:
            sub.add_argument(
                "--tipo",
                choices=["contrato", "unitarias", "arquitectura", "todas"],
                default="todas",
                help="tipo de prueba a correr",
            )
            sub.add_argument(
                "--sin-mongo",
                action="store_true",
                help="omite las pruebas marcadas mongo",
            )
            sub.add_argument("-v", "--verboso", action="store_true")

    return analizador


def main(argumentos: list[str] | None = None) -> int:
    analizador = construir_analizador()
    args = analizador.parse_args(argumentos)

    # Valores por omision para las tareas que no declaran estas banderas.
    for bandera, valor in (
        ("borrar_datos", False),
        ("tipo", "todas"),
        ("sin_mongo", False),
        ("verboso", False),
    ):
        if not hasattr(args, bandera):
            setattr(args, bandera, valor)

    try:
        args.funcion(args)
    except Fallo as fallo:
        print(f"\nERROR: {fallo}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
