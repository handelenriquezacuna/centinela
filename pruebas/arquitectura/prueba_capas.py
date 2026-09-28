"""Pruebas de arquitectura: las reglas de capas, verificadas y no confiadas.

Son pruebas sobre el codigo fuente, no sobre su comportamiento. Recorren el arbol de
`app/` con el modulo `ast` de la biblioteca estandar. No necesitan Mongo ni levantar
la aplicacion.

Las cuatro reglas:

1. `app/deteccion/` no importa nada de `app/api/`: el motor no sabe que existe una
   pantalla. Pasa en vacio mientras `app/deteccion/` no exista, y sigue pasando
   despues.
2. Ninguna consulta a Mongo fuera de `app/repos/`.
3. Un solo lugar construye el cliente: `app/nucleo/db.py`.
4. La URI de Mongo no esta escrita en el codigo, solo en la configuracion.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
APP = RAIZ / "app"
REPOS = APP / "repos"
DETECCION = APP / "deteccion"
DB = APP / "nucleo" / "db.py"


def modulos(directorio: Path) -> list[Path]:
    """Archivos .py de un directorio, recursivo. Vacio si el directorio no existe."""
    if not directorio.is_dir():
        return []
    return sorted(directorio.rglob("*.py"))


def arbol(ruta: Path) -> ast.Module:
    return ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))


def relativa(ruta: Path) -> str:
    """Ruta legible en el mensaje de fallo. Tolera archivos de fuera del repo,
    porque las contrapruebas escriben en un directorio temporal."""
    try:
        return str(ruta.relative_to(RAIZ))
    except ValueError:
        return str(ruta)


def importaciones(ruta: Path) -> list[tuple[str, int]]:
    """Modulos importados por un archivo, con su numero de linea."""
    encontradas: list[tuple[str, int]] = []
    for nodo in ast.walk(arbol(ruta)):
        if isinstance(nodo, ast.Import):
            encontradas.extend((alias.name, nodo.lineno) for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            encontradas.append((nodo.module, nodo.lineno))
    return encontradas


# ---------------------------------------------------------------------------
# 1. El motor no sabe que existe una pantalla
# ---------------------------------------------------------------------------


def prueba_deteccion_no_importa_la_api() -> None:
    """Regla escrita antes de que `app/deteccion/` exista.

    Mientras el paquete no exista, esta prueba pasa en vacio a proposito: asi la regla
    ya esta puesta cuando B empiece H-09, en vez de llegar despues a discutir un
    import que ya se escribio.

    Por que importa: si el motor importa la capa HTTP, deja de poder correr headless y
    se vuelve imposible probarlo sin levantar un servidor. El motor tiene que generar
    alertas correctas aunque el portal no exista.
    """
    infractores: list[str] = []

    for ruta in modulos(DETECCION):
        for modulo, linea in importaciones(ruta):
            if modulo == "app.api" or modulo.startswith("app.api."):
                infractores.append(f"{relativa(ruta)}:{linea} importa {modulo}")

    assert not infractores, (
        "app/deteccion/ no puede importar app/api/ (el motor es headless):\n"
        + "\n".join(infractores)
    )


def prueba_la_regla_de_deteccion_detecta_de_verdad(tmp_path: Path) -> None:
    """La prueba de arriba pasa en vacio; esta comprueba que no pasa por vacia.

    Sin esto, `prueba_deteccion_no_importa_la_api` seria verde para siempre por no
    tener nada que mirar, y el dia que `app/deteccion/` aparezca nadie sabria si de
    verdad revisa algo.
    """
    falso = tmp_path / "deteccion"
    falso.mkdir()
    (falso / "d1.py").write_text(
        "from app.api.errores import RespuestaError\n", encoding="utf-8"
    )

    encontrados = [
        modulo
        for ruta in modulos(falso)
        for modulo, _ in importaciones(ruta)
        if modulo.startswith("app.api")
    ]

    assert encontrados == ["app.api.errores"]


def prueba_los_modelos_no_importan_la_api_ni_los_repos() -> None:
    """El dominio es el centro: no depende de como se guarda ni de como se publica."""
    infractores: list[str] = []

    for ruta in modulos(APP / "modelos"):
        for modulo, linea in importaciones(ruta):
            if modulo.startswith(("app.api", "app.repos", "pymongo", "bson")):
                infractores.append(f"{relativa(ruta)}:{linea} importa {modulo}")

    assert not infractores, "app/modelos/ no depende de HTTP ni del driver:\n" + "\n".join(
        infractores
    )


def prueba_el_nucleo_no_importa_la_api() -> None:
    infractores = [
        f"{relativa(ruta)}:{linea} importa {modulo}"
        for ruta in modulos(APP / "nucleo")
        for modulo, linea in importaciones(ruta)
        if modulo.startswith("app.api")
    ]

    assert not infractores, "app/nucleo/ es la base, no puede depender de la capa HTTP"


def prueba_la_aplicacion_no_importa_las_pruebas() -> None:
    """El conjunto de demo vive en `pruebas/`: la dependencia va en un solo sentido."""
    infractores = [
        f"{relativa(ruta)}:{linea} importa {modulo}"
        for ruta in modulos(APP)
        for modulo, linea in importaciones(ruta)
        if modulo.startswith("pruebas")
    ]

    assert not infractores, "app/ no puede importar pruebas/:\n" + "\n".join(infractores)


# ---------------------------------------------------------------------------
# 2. Ninguna consulta fuera de app/repos/
# ---------------------------------------------------------------------------

# Metodos del driver que son inequivocos: ninguno se llama igual que un metodo comun
# de la biblioteca estandar. A proposito NO estan `get` ni `count` (dict.get,
# list.count), que darian falsos positivos y harian que la prueba se ignore.
METODOS_DE_CONSULTA = frozenset(
    {
        "find",
        "find_one",
        "find_one_and_delete",
        "find_one_and_replace",
        "find_one_and_update",
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "replace_one",
        "delete_one",
        "delete_many",
        "aggregate",
        "count_documents",
        "estimated_document_count",
        "distinct",
        "bulk_write",
        "watch",
        "command",
        "create_index",
        "create_indexes",
        "drop_index",
        "list_collections",
        "list_collection_names",
        "create_collection",
        "drop_collection",
        "drop_database",
        "with_transaction",
        "start_session",
    }
)


def consultas_en(ruta: Path) -> list[str]:
    halladas: list[str] = []
    for nodo in ast.walk(arbol(ruta)):
        if not isinstance(nodo, ast.Call):
            continue
        if isinstance(nodo.func, ast.Attribute) and nodo.func.attr in METODOS_DE_CONSULTA:
            halladas.append(f"{relativa(ruta)}:{nodo.lineno} llama .{nodo.func.attr}()")
    return halladas


def prueba_ninguna_consulta_fuera_de_repos() -> None:
    """La regla que mantiene contestable la pregunta "que consultas hace el sistema".

    Cuando las consultas se dispersan por routers y servicios, nadie puede responderla
    sin leer el proyecto completo, y afinar un indice se vuelve una caceria.
    """
    infractores = [
        hallada
        for ruta in modulos(APP)
        if REPOS not in ruta.parents
        for hallada in consultas_en(ruta)
    ]

    assert not infractores, (
        "hay consultas a Mongo fuera de app/repos/:\n"
        + "\n".join(infractores)
        + "\nMover la consulta a app/repos/ y llamarla desde ahi."
    )


def prueba_la_regla_de_consultas_detecta_de_verdad(tmp_path: Path) -> None:
    """Que la prueba anterior no pase por no mirar nada."""
    falso = tmp_path / "servicio.py"
    falso.write_text(
        "async def malo(base):\n"
        "    return await base['alertas'].find_one({'_id': 'ALR-001'})\n",
        encoding="utf-8",
    )

    halladas = consultas_en(falso)

    assert len(halladas) == 1
    assert "find_one" in halladas[0]


def prueba_los_repos_si_tienen_consultas() -> None:
    """Contraprueba: si `app/repos/` no tuviera consultas, la regla seria decorativa."""
    halladas = [hallada for ruta in modulos(REPOS) for hallada in consultas_en(ruta)]

    assert halladas, "app/repos/ deberia ser el lugar donde SI hay consultas"


def prueba_el_driver_solo_se_importa_donde_corresponde() -> None:
    """`pymongo` solo en `app/repos/` y en `app/nucleo/db.py`.

    Un import de pymongo en un router es el primer paso de una consulta ahi mismo.
    """
    permitidos = {DB}
    infractores: list[str] = []

    for ruta in modulos(APP):
        if REPOS in ruta.parents or ruta in permitidos:
            continue
        for modulo, linea in importaciones(ruta):
            if modulo.split(".")[0] in {"pymongo", "bson"}:
                infractores.append(f"{relativa(ruta)}:{linea} importa {modulo}")

    # app/api/dependencias.py necesita el tipo para anotar la dependencia inyectada,
    # que no es una consulta. Se acepta solo ahi y solo para tipos.
    tolerados = {"app/api/dependencias.py"}
    infractores = [
        infractor
        for infractor in infractores
        if infractor.split(":")[0] not in tolerados
    ]

    assert not infractores, "pymongo solo va en app/repos/ y app/nucleo/db.py:\n" + "\n".join(
        infractores
    )


# ---------------------------------------------------------------------------
# 3 y 4. Un solo cliente, una sola URI
# ---------------------------------------------------------------------------


def prueba_solo_db_py_construye_el_cliente() -> None:
    """Un cliente por proceso. Uno por peticion agota los sockets del servidor."""
    infractores: list[str] = []

    for ruta in modulos(APP):
        for nodo in ast.walk(arbol(ruta)):
            if not isinstance(nodo, ast.Call):
                continue
            nombre = None
            if isinstance(nodo.func, ast.Name):
                nombre = nodo.func.id
            elif isinstance(nodo.func, ast.Attribute):
                nombre = nodo.func.attr
            if nombre in {"AsyncMongoClient", "MongoClient"} and ruta != DB:
                infractores.append(f"{relativa(ruta)}:{nodo.lineno} construye {nombre}")

    assert not infractores, (
        "solo app/nucleo/db.py puede construir el cliente de Mongo:\n"
        + "\n".join(infractores)
    )


def prueba_motor_no_se_usa_en_ningun_lado() -> None:
    """Motor esta deprecado con fin de vida en mayo de 2026. El driver es pymongo."""
    infractores = [
        f"{relativa(ruta)}:{linea} importa {modulo}"
        for ruta in modulos(APP)
        for modulo, linea in importaciones(ruta)
        if modulo.split(".")[0] in {"motor", "mongoengine", "beanie"}
    ]

    assert not infractores, "el driver es pymongo.AsyncMongoClient:\n" + "\n".join(
        infractores
    )


def prueba_la_uri_no_esta_escrita_en_el_codigo() -> None:
    """La URI sale de la configuracion. Una URI en el codigo es un ambiente hundido.

    Se revisa el texto y no el AST porque lo que se busca es la cadena, en cualquier
    forma en la que alguien la escriba.
    """
    infractores: list[str] = []

    for ruta in modulos(APP):
        for numero, linea in enumerate(
            ruta.read_text(encoding="utf-8").splitlines(), start=1
        ):
            sin_comentario = linea.split("#", 1)[0]
            if "mongodb://" in sin_comentario or "mongodb+srv://" in sin_comentario:
                infractores.append(f"{relativa(ruta)}:{numero}")

    assert not infractores, (
        "la URI de Mongo va en config/centinela.yml, no en el codigo:\n"
        + "\n".join(infractores)
    )


def prueba_solo_config_py_lee_el_entorno_y_el_yaml() -> None:
    """Un solo lugar lee entorno y YAML; el resto recibe `Ajustes` ya armado.

    Si cada modulo lee `os.environ`, la precedencia de configuracion deja de existir
    como contrato y pasa a ser lo que cada archivo decidio.
    """
    config = APP / "nucleo" / "config.py"
    infractores: list[str] = []

    for ruta in modulos(APP):
        if ruta == config:
            continue
        texto = ruta.read_text(encoding="utf-8")
        for numero, linea in enumerate(texto.splitlines(), start=1):
            sin_comentario = linea.split("#", 1)[0]
            for patron in ("os.environ", "os.getenv", "yaml.safe_load", "yaml.load"):
                if patron in sin_comentario:
                    infractores.append(f"{relativa(ruta)}:{numero} usa {patron}")

    assert not infractores, (
        "solo app/nucleo/config.py lee entorno o YAML:\n" + "\n".join(infractores)
    )


# ---------------------------------------------------------------------------
# Higiene del arbol
# ---------------------------------------------------------------------------


def prueba_no_existe_el_paquete_motor() -> None:
    """El paquete se llama `app/deteccion/`, para no confundirlo con Motor."""
    assert not (APP / "motor").exists(), (
        "el paquete del motor de deteccion se llama app/deteccion/, no app/motor/"
    )


@pytest.mark.parametrize(
    "carpeta", ["api", "modelos", "nucleo", "repos"]
)
def prueba_los_paquetes_de_app_existen(carpeta: str) -> None:
    assert (APP / carpeta / "__init__.py").is_file()


def prueba_no_hay_emojis_en_el_codigo_ni_en_las_pruebas() -> None:
    """Regla del repo: sin emojis en ningun lado.

    Se revisa por rango Unicode y no por una lista, para que no haya que mantener un
    catalogo de emojis.
    """
    rangos = (
        (0x1F300, 0x1FAFF),
        (0x1F000, 0x1F2FF),
        (0x2600, 0x27BF),
        (0xFE0F, 0xFE0F),
        (0x2B00, 0x2BFF),
    )

    infractores: list[str] = []
    for ruta in [*modulos(APP), *modulos(RAIZ / "pruebas"), RAIZ / "tareas.py"]:
        if not ruta.is_file():
            continue
        for numero, linea in enumerate(
            ruta.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for caracter in linea:
                punto = ord(caracter)
                if any(inicio <= punto <= fin for inicio, fin in rangos):
                    infractores.append(
                        f"{relativa(ruta)}:{numero} tiene {caracter!r}"
                    )

    assert not infractores, "sin emojis en ningun lado:\n" + "\n".join(infractores)
