"""T-52 · writeConcern y readConcern: lo que de verdad viaja al servidor.

Declarar el concern en el cliente no prueba nada si una operacion lo pisa o si el
driver no lo envia. Por eso aqui se mira el comando que sale por el cable, con un
monitor de comandos del driver, en las cuatro operaciones donde la consistencia
importa:

- la alerta de D1 (`insertar_alerta`) y su token (`guardar_token`);
- la transicion de una alerta (`find_one_and_update`);
- la transaccion de escalar (readConcern al abrirla, writeConcern al confirmarla).

La decision (cliente y no operacion por operacion) esta justificada en
`app/nucleo/db.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from pymongo import AsyncMongoClient, monitoring

from app.deteccion.d1 import NOMBRE_D1
from app.modelos import EstadoAlerta
from app.nucleo.config import Ajustes
from app.nucleo.db import (
    ESCRITURA_TIEMPO_MS,
    ESCRITURA_W,
    LECTURA_NIVEL,
    crear_cliente,
)
from app.repos.alertas import RepoAlertas, planear_transicion
from app.repos.carga_demo import cargar_demo
from app.repos.deteccion import RepoDeteccion
from pruebas.dobles.datos_demo import DATOS_DEMO
from pruebas.integracion.prueba_d1_mongo import BASE_D1, base_d1  # noqa: F401 - fixture

pytestmark = pytest.mark.mongo

AHORA = datetime(2026, 9, 25, 5, 0, tzinfo=UTC)
ESCRITURA_ESPERADA = {"w": ESCRITURA_W, "wtimeout": ESCRITURA_TIEMPO_MS}
LECTURA_ESPERADA = {"level": LECTURA_NIVEL}
COMANDOS_DE_ESCRITURA = {"insert", "update", "findAndModify", "commitTransaction"}


class Anotador(monitoring.CommandListener):
    """Guarda cada comando que el driver envia, en orden."""

    def __init__(self) -> None:
        self.comandos: list[tuple[str, dict[str, Any]]] = []

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        self.comandos.append((event.command_name, dict(event.command)))

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:
        pass

    def failed(self, event: monitoring.CommandFailedEvent) -> None:
        pass

    def limpiar(self) -> None:
        self.comandos.clear()

    def de(self, nombre: str) -> list[dict[str, Any]]:
        return [comando for clave, comando in self.comandos if clave == nombre]


@pytest_asyncio.fixture
async def escuchada(base_d1: Any, ajustes: Ajustes) -> AsyncIterator[tuple[Any, Anotador]]:  # noqa: F811
    """La base de D1 (ya sembrada) vista por un cliente de la fabrica real, con monitor."""
    anotador = Anotador()
    cliente = crear_cliente(ajustes, escuchas=[anotador])
    try:
        yield cliente[BASE_D1], anotador
    finally:
        await cliente.close()


def _plan(actual: dict[str, Any], destino: EstadoAlerta, agente: str) -> Any:
    return planear_transicion(actual, destino=destino, agente_id=agente, nota="", ahora=AHORA)


def prueba_la_configuracion_efectiva_del_cliente_y_de_las_colecciones(ajustes: Ajustes) -> None:
    cliente = crear_cliente(ajustes)
    coleccion = cliente[ajustes.mongo.base]["alertas"]
    assert cliente.write_concern.document == ESCRITURA_ESPERADA
    assert cliente.read_concern.level == LECTURA_NIVEL
    # La coleccion hereda del cliente a traves de la base: nadie tiene que repetirlo.
    assert coleccion.write_concern.document == ESCRITURA_ESPERADA
    assert coleccion.read_concern.level == LECTURA_NIVEL


@pytest.mark.asyncio
async def prueba_la_alerta_y_el_token_de_d1_se_escriben_con_majority(
    escuchada: tuple[Any, Anotador],
) -> None:
    base, anotador = escuchada
    repo = RepoDeteccion(base)
    alerta = DATOS_DEMO["alertas"][0]

    anotador.limpiar()
    assert await repo.insertar_alerta(alerta) is True
    inserciones = [c for c in anotador.de("insert") if c["insert"] == "alertas"]
    assert len(inserciones) == 1
    assert inserciones[0]["writeConcern"] == ESCRITURA_ESPERADA

    anotador.limpiar()
    await repo.guardar_token(NOMBRE_D1, {"_data": "82AAAA"}, transaccion_id=None)
    actualizaciones = [c for c in anotador.de("update") if c["update"] == "deteccion_estado"]
    assert len(actualizaciones) == 1
    assert actualizaciones[0]["writeConcern"] == ESCRITURA_ESPERADA

    anotador.limpiar()
    assert await repo.leer_token(NOMBRE_D1) == {"_data": "82AAAA"}
    lecturas = [c for c in anotador.de("find") if c["find"] == "deteccion_estado"]
    assert lecturas and lecturas[0]["readConcern"] == LECTURA_ESPERADA


@pytest.mark.asyncio
async def prueba_la_transicion_se_escribe_con_majority_y_lee_con_majority(
    escuchada: tuple[Any, Anotador],
) -> None:
    base, anotador = escuchada
    await cargar_demo(base, {"alertas": DATOS_DEMO["alertas"][1:]})
    repo = RepoAlertas(base)
    actual = await repo.obtener("ALR-002")

    anotador.limpiar()
    await repo.aplicar_transicion(_plan(actual, EstadoAlerta.EN_REVISION, "AGT-001"))

    modificaciones = anotador.de("findAndModify")
    assert len(modificaciones) == 1
    assert modificaciones[0]["writeConcern"] == ESCRITURA_ESPERADA

    anotador.limpiar()
    await repo.obtener("ALR-002")
    assert anotador.de("find")[0]["readConcern"] == LECTURA_ESPERADA


@pytest.mark.asyncio
async def prueba_la_transaccion_de_escalar_abre_con_majority_y_confirma_con_majority(
    escuchada: tuple[Any, Anotador],
) -> None:
    base, anotador = escuchada
    await cargar_demo(base, {"alertas": DATOS_DEMO["alertas"][1:]})
    repo = RepoAlertas(base)
    actual = await repo.obtener("ALR-003")

    anotador.limpiar()
    await repo.aplicar_transicion(_plan(actual, EstadoAlerta.ESCALADA, "AGT-002"))

    # Dentro de la transaccion: el readConcern viaja en el primer comando (el que la
    # abre, con startTransaction) y el writeConcern solo en el commit.
    abiertura = next(c for _, c in anotador.comandos if c.get("startTransaction"))
    assert abiertura["readConcern"]["level"] == LECTURA_NIVEL
    confirmacion = anotador.de("commitTransaction")
    assert len(confirmacion) == 1
    assert confirmacion[0]["writeConcern"]["w"] == ESCRITURA_W
    for nombre in ("insert", "findAndModify"):
        for comando in anotador.de(nombre):
            assert "writeConcern" not in comando, "dentro de una transaccion lo prohibe el servidor"

    assert await base["casos"].count_documents({"_id": "CAS-003"}) == 1


@pytest.mark.asyncio
async def prueba_control_un_cliente_sin_concern_no_lo_envia(
    base_d1: Any, ajustes: Ajustes  # noqa: F811
) -> None:
    """Contraste: la prueba detecta la ausencia. Un cliente pelado no manda writeConcern."""
    anotador = Anotador()
    pelado = AsyncMongoClient(ajustes.mongo.uri, tz_aware=True, event_listeners=[anotador])
    try:
        anotador.limpiar()
        await pelado[BASE_D1]["control"].insert_one({"_id": 1})
        inserciones = [c for c in anotador.de("insert") if c["insert"] == "control"]
        assert inserciones and "writeConcern" not in inserciones[0]
    finally:
        await pelado.close()
