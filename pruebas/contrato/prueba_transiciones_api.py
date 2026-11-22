"""Contrato de `POST /api/v1/alertas/{alerta_id}/transiciones` (H-14), sin Mongo.

El repositorio es el doble en memoria, que aplica el mismo plan que arma
`planear_transicion`. Lo que se verifica aqui es el contrato HTTP: cada uno de los 25
pares de la maquina de estados (4 validos, 21 rechazados), el historial que se agrega,
los 409 de conflicto, el 404, el 422 y la forma unica del error. La escritura real (el
`$push` condicionado y la transaccion del caso) la prueba
`pruebas/integracion/prueba_transiciones_mongo.py`.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from itertools import product
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_repo_alertas
from app.main import crear_aplicacion
from app.modelos import TRANSICIONES, EstadoAlerta
from app.repos.alertas import ProtocoloRepoAlertas, RepoAlertas
from pruebas.dobles.datos_demo import alertas_como_documentos
from pruebas.dobles.repos_falsos import RepoAlertasFalso

AGENTE = "AGT-001"


def _ruta(alerta_id: str) -> str:
    return f"/api/v1/alertas/{alerta_id}/transiciones"


def _alerta_en(estado: EstadoAlerta) -> dict[str, Any]:
    """ALR-002 del demo, puesta en `estado` y, si ya la tomaron, en manos de AGENTE."""
    documento = copy.deepcopy(next(d for d in alertas_como_documentos() if d["_id"] == "ALR-002"))
    documento["estado"] = estado.value
    if estado != EstadoAlerta.NUEVA:
        documento["agente_id"] = AGENTE
    return documento


@pytest_asyncio.fixture
async def armar() -> AsyncIterator[Any]:
    """Arma un cliente con el doble que se le pase; devuelve (cliente, doble)."""
    abiertos: list[Any] = []

    async def _armar(documentos: list[dict[str, Any]]) -> tuple[AsyncClient, RepoAlertasFalso]:
        repo = RepoAlertasFalso(documentos)
        aplicacion = crear_aplicacion()
        aplicacion.dependency_overrides[dep_repo_alertas] = lambda: repo
        gestor = LifespanManager(aplicacion)
        gestionada = await gestor.__aenter__()
        cliente = AsyncClient(transport=ASGITransport(app=gestionada.app), base_url="http://p")
        abiertos.append((gestor, cliente))
        return cliente, repo

    yield _armar
    for gestor, cliente in abiertos:
        await cliente.aclose()
        await gestor.__aexit__(None, None, None)


def prueba_el_doble_cumple_el_protocolo_de_transiciones() -> None:
    import inspect

    assert isinstance(RepoAlertasFalso([]), ProtocoloRepoAlertas)
    for metodo in ("obtener", "aplicar_transicion"):
        del_protocolo = inspect.signature(getattr(ProtocoloRepoAlertas, metodo))
        assert set(inspect.signature(getattr(RepoAlertas, metodo)).parameters) == set(
            del_protocolo.parameters
        )
        assert set(inspect.signature(getattr(RepoAlertasFalso, metodo)).parameters) == set(
            del_protocolo.parameters
        )


PARES = list(product(EstadoAlerta, EstadoAlerta))


@pytest.mark.asyncio
@pytest.mark.parametrize(("origen", "destino"), PARES, ids=[f"{o}-{d}" for o, d in PARES])
async def prueba_cada_par_de_la_maquina_de_estados(
    armar: Any, origen: EstadoAlerta, destino: EstadoAlerta
) -> None:
    cliente, repo = await armar([_alerta_en(origen)])
    antes = await repo.obtener("ALR-002")

    respuesta = await cliente.post(
        _ruta("ALR-002"), json={"destino": destino, "agente_id": AGENTE, "nota": "prueba"}
    )

    if destino in TRANSICIONES[origen]:
        assert respuesta.status_code == status.HTTP_200_OK, respuesta.text
        alerta = respuesta.json()["alerta"]
        assert alerta["estado"] == destino
        assert alerta["agente_id"] == AGENTE
        # Se AGREGA: lo de antes intacto, una entrada nueva al final.
        previas = [(e["accion"], e.get("agente_id"), e["nota"]) for e in antes["historial"]]
        assert [
            (e["accion"], e["agente_id"], e["nota"]) for e in alerta["historial"][:-1]
        ] == previas
        assert alerta["historial"][-1]["accion"] == destino
        assert alerta["historial"][-1]["nota"] == "prueba"
        assert len(alerta["historial"]) == len(antes["historial"]) + 1
    else:
        assert respuesta.status_code == status.HTTP_409_CONFLICT
        error = respuesta.json()["error"]
        assert error["codigo"] == "transicion_invalida"
        assert error["detalles"] == [{"campo": "estado", "mensaje": f"estado actual: {origen}"}]
        assert repo.escrituras == 0


@pytest.mark.asyncio
async def prueba_escalar_devuelve_el_caso_creado(armar: Any) -> None:
    cliente, repo = await armar([_alerta_en(EstadoAlerta.EN_REVISION)])

    respuesta = await cliente.post(
        _ruta("ALR-002"), json={"destino": "escalada", "agente_id": AGENTE}
    )

    assert respuesta.status_code == status.HTTP_200_OK
    assert respuesta.json()["caso_id"] == "CAS-002"
    assert repo.casos["CAS-002"]["alertas"] == ["ALR-002"]


@pytest.mark.asyncio
async def prueba_resolver_la_de_otro_agente_es_conflicto(armar: Any) -> None:
    cliente, repo = await armar([_alerta_en(EstadoAlerta.EN_REVISION)])

    respuesta = await cliente.post(
        _ruta("ALR-002"), json={"destino": "confirmada", "agente_id": "AGT-002"}
    )

    assert respuesta.status_code == status.HTTP_409_CONFLICT
    assert respuesta.json()["error"]["codigo"] == "conflicto"
    assert repo.escrituras == 0


@pytest.mark.asyncio
async def prueba_si_otro_la_tomo_en_el_medio_es_conflicto(armar: Any) -> None:
    cliente, repo = await armar([_alerta_en(EstadoAlerta.NUEVA)])
    repo.interferir_antes_de_transicionar = True

    respuesta = await cliente.post(
        _ruta("ALR-002"), json={"destino": "en_revision", "agente_id": AGENTE}
    )

    assert respuesta.status_code == status.HTTP_409_CONFLICT
    assert respuesta.json()["error"]["codigo"] == "conflicto"
    assert repo.escrituras == 0


@pytest.mark.asyncio
async def prueba_estado_esperado_distinto_es_conflicto_sin_escribir(armar: Any) -> None:
    cliente, repo = await armar([_alerta_en(EstadoAlerta.EN_REVISION)])

    respuesta = await cliente.post(
        _ruta("ALR-002"),
        json={"destino": "confirmada", "agente_id": AGENTE, "estado_esperado": "nueva"},
    )

    assert respuesta.status_code == status.HTTP_409_CONFLICT
    assert respuesta.json()["error"]["detalles"][0]["mensaje"] == "estado actual: en_revision"
    assert repo.escrituras == 0


@pytest.mark.asyncio
async def prueba_alerta_inexistente_es_404(armar: Any) -> None:
    cliente, _ = await armar([])

    respuesta = await cliente.post(
        _ruta("ALR-999"), json={"destino": "en_revision", "agente_id": AGENTE}
    )

    assert respuesta.status_code == status.HTTP_404_NOT_FOUND
    assert respuesta.json()["error"]["codigo"] == "no_encontrado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        pytest.param({"destino": "resuelta", "agente_id": AGENTE}, id="estado-inexistente"),
        pytest.param({"destino": "en_revision", "agente_id": "Jose"}, id="agente-mal-formado"),
        pytest.param({"destino": "en_revision"}, id="sin-agente"),
        pytest.param(
            {"destino": "en_revision", "agente_id": AGENTE, "estado": "nueva"}, id="campo-extra"
        ),
    ],
)
async def prueba_un_cuerpo_invalido_es_422_con_la_forma_unica(
    armar: Any, cuerpo: dict[str, Any]
) -> None:
    cliente, repo = await armar([_alerta_en(EstadoAlerta.NUEVA)])

    respuesta = await cliente.post(_ruta("ALR-002"), json=cuerpo)

    assert respuesta.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert set(respuesta.json()) == {"error"}
    assert respuesta.json()["error"]["codigo"] == "parametros_invalidos"
    assert repo.escrituras == 0


@pytest.mark.asyncio
async def prueba_la_lista_lee_v1_y_v2_a_la_vez(armar: Any) -> None:
    """Lectura dual por la API (H-26): una v1 del demo y una v2 como la de D1."""
    v2 = {
        **copy.deepcopy(alertas_como_documentos()[1]),
        "_id": "ALR-1001",
        "transaccion_id": "TXN-1001",
        "esquema_version": 2,
        "reglas_disparadas": [
            {
                "regla_id": "REG-003",
                "codigo": "R-05",
                "version": 1,
                "peso": 15,
                "explicacion": "22:47 fuera del horario habitual 07:00-19:00",
            }
        ],
        "politica": {"politica_id": "POL-001", "version": 1},
    }
    cliente, _ = await armar([alertas_como_documentos()[0], v2])

    elementos = (await cliente.get("/api/v1/alertas")).json()["elementos"]
    por_id = {e["_id"]: e for e in elementos}

    assert por_id["ALR-001"]["esquema_version"] == 1
    assert por_id["ALR-001"]["reglas_disparadas"] == ["REG-001", "REG-002", "REG-003"]
    assert por_id["ALR-1001"]["esquema_version"] == 2
    assert por_id["ALR-1001"]["reglas_disparadas"][0]["peso"] == 15
    assert por_id["ALR-1001"]["politica"] == {"politica_id": "POL-001", "version": 1}
