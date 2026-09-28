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
    assert len(publicados) == 3, "mongo1 publica los tres puertos por los tres nodos"


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
