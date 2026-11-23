"""Contratos de `infra/docker-compose.yml` que hacen que el arranque funcione en una
maquina limpia.

Se leen del archivo, sin Docker: son propiedades del archivo, no del clúster.

Existen porque el error que previenen ya se cometio: declarar los volumenes
`external: true` para conservar una siembra. Compose NO crea un volumen externo, asi
que en la maquina de quien clona el repo por primera vez `up` falla con
`external volume "..." not found` antes de arrancar un contenedor. Verificar el arranque
en una maquina que ya tiene los volumenes de corridas anteriores esconde exactamente
ese fallo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = RAIZ / "infra" / "docker-compose.yml"

NODOS = ("mongo1", "mongo2", "mongo3")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def prueba_ningun_volumen_es_externo(compose: dict) -> None:
    """La regla que sostiene "arranque probado en maquina limpia".

    Un volumen `external` es una precondicion que el repo no puede cumplir por si
    mismo. Si hace falta crear algo a mano antes del primer `up`, el arranque no es de
    un comando.
    """
    externos = [
        nombre
        for nombre, definicion in (compose.get("volumes") or {}).items()
        if isinstance(definicion, dict) and definicion.get("external")
    ]

    assert not externos, (
        f"volumenes declarados external: {externos}. Compose no los crea, asi que "
        "`up` falla en una maquina limpia con 'external volume not found'. "
        "Dejarlos sin `external` y sin `name`: Compose los administra."
    )


def prueba_ningun_volumen_fija_un_nombre_a_mano(compose: dict) -> None:
    """Un nombre fijo ata el volumen a la historia de una maquina.

    Compose deriva el nombre del proyecto, que ya es explicito. Fijarlo aparte solo
    sirve para adoptar volumenes viejos, y para eso ya se vio que el precio es romper
    la maquina limpia.
    """
    con_nombre = {
        nombre: definicion["name"]
        for nombre, definicion in (compose.get("volumes") or {}).items()
        if isinstance(definicion, dict) and definicion.get("name")
    }

    assert not con_nombre, f"volumenes con nombre fijado a mano: {con_nombre}"


def prueba_los_tres_nodos_declaran_su_volumen(compose: dict) -> None:
    """Sin volumen, los datos viven en la capa de escritura del contenedor y un
    `down` normal se los lleva."""
    declarados = set(compose.get("volumes") or {})

    assert declarados == {f"{nodo}data" for nodo in NODOS}


def prueba_el_proyecto_tiene_nombre_explicito(compose: dict) -> None:
    """Sin `name`, el proyecto depende de la ruta donde este clonado el repo.

    Ojo con lo que esta clave NO hace: no evita que dos checkouts del repo se peleen
    los contenedores, porque comparten este archivo y ahora los dos usan `centinela`.
    Eso lo evita la regla de que solo el checkout principal corre Compose.
    """
    assert compose.get("name") == "centinela"


def prueba_los_nodos_comparten_el_espacio_de_red_del_primero(compose: dict) -> None:
    """`network_mode: service:mongo1` es lo que permite una sola URI para las dos
    modalidades de arranque, y lo que evita tocar el archivo hosts del sistema."""
    servicios = compose["services"]

    for nodo in NODOS[1:]:
        assert servicios[nodo]["network_mode"] == "service:mongo1"

    publicados = servicios["mongo1"]["ports"]
    for puerto in ("27018:27018", "27019:27019", "27020:27020"):
        assert f"127.0.0.1:{puerto}" in publicados, f"mongo1 no publica {puerto} en 127.0.0.1"


def prueba_el_servicio_app_comparte_el_espacio_de_red_de_mongo1(compose: dict) -> None:
    """La segunda modalidad de arranque (H-00B): `app` entra al espacio de red de
    mongo1 igual que mongo2 y mongo3, y NO publica su propio puerto.

    Un servicio con `network_mode: service:X` no tiene pila de red propia, asi que no
    puede declarar `ports`: el puerto 8000 tiene que estar en la lista de mongo1, el
    dueno del espacio de red. Es lo mismo que ya hace 27019 y 27020 por mongo2 y
    mongo3, aplicado a la API.
    """
    servicios = compose["services"]

    assert "app" in servicios, "falta el servicio app en infra/docker-compose.yml"
    assert servicios["app"]["network_mode"] == "service:mongo1"
    assert "ports" not in servicios["app"], (
        "el puerto de la API se publica en mongo1, no en app: un servicio con "
        "network_mode: service:X no puede declarar sus propios ports"
    )

    assert "127.0.0.1:8000:8000" in servicios["mongo1"]["ports"], (
        "mongo1 tiene que publicar tambien el 8000 de la API"
    )


def prueba_el_servicio_app_no_arranca_con_un_up_sin_argumentos(compose: dict) -> None:
    """`app` lleva un perfil explicito para no sorprender a quien solo quiere Mongo.

    La modalidad A (Mongo en Compose, Python en el host) es la que el equipo usa a
    diario y es la que un `docker compose up` sin mas tiene que seguir levantando.
    Sin `profiles`, agregar `app` habria convertido ese comando en otra cosa para
    todo el mundo.
    """
    assert compose["services"]["app"].get("profiles") == ["app"]


def prueba_el_replica_set_se_llama_rsfraude(compose: dict) -> None:
    """El nombre esta en la URI del YAML de configuracion: tienen que coincidir."""
    from app.nucleo.config import Ajustes

    for nodo in NODOS:
        assert "--replSet" in compose["services"][nodo]["command"]
        assert "rsfraude" in compose["services"][nodo]["command"]

    assert "replicaSet=rsfraude" in Ajustes().mongo.uri


def prueba_los_puertos_no_usan_el_27017(compose: dict) -> None:
    """El 27017 se deja libre a proposito, para no chocar con un MongoDB instalado
    en la maquina de quien sea."""
    publicados = compose["services"]["mongo1"]["ports"]

    assert not any("27017" in str(puerto) for puerto in publicados)


# ---------------------------------------------------------------------------
# H-25: autenticacion y secretos
# ---------------------------------------------------------------------------

BASES_FUERA_DE_ANTIFRAUDE = re.compile(r"antifraude_(?:pruebas[a-z0-9_]*|validadores_tmp)")


def prueba_ningun_puerto_se_publica_fuera_de_localhost(compose: dict) -> None:
    """T-02: Mongo y la API solo escuchan en la interfaz local del anfitrion."""
    for nodo, servicio in compose["services"].items():
        for puerto in servicio.get("ports", []):
            assert str(puerto).startswith("127.0.0.1:"), (
                f"{nodo} publica {puerto} en todas las interfaces"
            )


def prueba_los_tres_nodos_arrancan_con_el_punto_de_entrada_de_la_autenticacion(
    compose: dict,
) -> None:
    """El keyfile lo agrega `infra/mongo-entrada.sh`, igual en los tres nodos."""
    for nodo in NODOS:
        servicio = compose["services"][nodo]
        assert servicio["entrypoint"] == ["bash", "/entrada/mongo-entrada.sh"]
        volumenes = " ".join(servicio["volumes"])
        assert "./secretos:/secretos:ro" in volumenes
        assert "./mongo-entrada.sh:/entrada/mongo-entrada.sh:ro" in volumenes

    entrada = (RAIZ / "infra" / "mongo-entrada.sh").read_text(encoding="utf-8")
    assert "--keyFile" in entrada
    assert "CENTINELA_SIN_AUTH" in entrada


def prueba_los_secretos_no_se_versionan() -> None:
    """.env, el keyfile y todo infra/secretos/ (salvo .gitkeep) estan ignorados."""
    ignorado = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignorado
    assert "infra/secretos/*" in ignorado


def prueba_el_molde_del_entorno_no_trae_valores() -> None:
    molde = (RAIZ / ".env.example").read_text(encoding="utf-8")
    claves = [linea for linea in molde.splitlines() if linea and not linea.startswith("#")]
    assert claves, ".env.example tiene que listar las variables"
    for linea in claves:
        nombre, _, valor = linea.partition("=")
        assert nombre and not valor.strip(), f"{nombre} trae un valor en el molde"


def prueba_la_configuracion_versionada_no_trae_credenciales() -> None:
    """La URI con usuario llega por CENTINELA_MONGO__URI; el YAML no lleva secretos."""
    from app.nucleo.config import RUTA_RAIZ

    yaml_base = (RUTA_RAIZ / "config" / "centinela.yml").read_text(encoding="utf-8")
    uri = yaml.safe_load(yaml_base)["mongo"]["uri"]
    assert "@" not in uri, "la URI del YAML no puede llevar usuario ni contrasena"
    assert not re.search(r"(?i)(password|contrasena|clave|secret)\s*:", yaml_base)


def prueba_los_scripts_de_usuarios_no_traen_contrasenas() -> None:
    script = (RAIZ / "scripts" / "semilla" / "04-usuarios.js").read_text(encoding="utf-8")
    assert not re.search(r"pwd:\s*[\"']", script), "una contrasena escrita en el script"


def prueba_cada_base_de_pruebas_esta_en_la_lista_del_usuario_de_pruebas() -> None:
    """centinela_pruebas es dbOwner solo de las bases que lista 04-usuarios.js.

    Si una prueba nueva crea otra base `antifraude_pruebas_*`, con la autenticacion
    encendida fallaria con "not authorized". Esta prueba lo avisa antes.
    """
    script = (RAIZ / "scripts" / "semilla" / "04-usuarios.js").read_text(encoding="utf-8")
    usadas: set[str] = set()
    for ruta in [*(RAIZ / "pruebas").rglob("*.py"), *(RAIZ / "scripts").rglob("*.js")]:
        if ruta.name == "04-usuarios.js" or ruta.name == "prueba_infraestructura.py":
            continue
        usadas |= set(BASES_FUERA_DE_ANTIFRAUDE.findall(ruta.read_text(encoding="utf-8")))
    faltan = {base for base in usadas if f'"{base}"' not in script}
    assert not faltan, f"agregar a 04-usuarios.js: {sorted(faltan)}"
