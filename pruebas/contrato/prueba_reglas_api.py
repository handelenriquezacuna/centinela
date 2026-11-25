"""Contrato de `/api/v1/reglas` (H-16), con el repositorio sustituido por un doble.

Corre sin Mongo: aca se verifica el contrato HTTP (rutas, versionado, errores con la
forma unica). Que el validador real acepte el documento versionado y que la escritura
filtre por version contra Mongo de verdad lo verifica `prueba_reglas_mongo.py`.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient

from app.api.dependencias import dep_repo_reglas
from app.deteccion import evaluador
from app.deteccion.evaluador import EVALUADORES, TIPOS_REGLA
from app.main import crear_aplicacion
from app.repos.reglas import ProtocoloRepoReglas, RepoReglas, siguiente_regla_id
from pruebas.dobles.catalogo_reglas import (
    cuerpo_simulacion_txn_001,
    politica_como_documento,
    reglas_como_documentos,
)
from pruebas.dobles.repos_falsos import RepoReglasFalso

RUTA = "/api/v1/reglas"


@pytest.fixture
def repo_reglas() -> RepoReglasFalso:
    return RepoReglasFalso(reglas_como_documentos(), politica_como_documento())


@pytest.fixture
def aplicacion(repo_reglas: RepoReglasFalso) -> FastAPI:
    """Sustituye la fixture de conftest: aca el doble es el de reglas."""
    creada = crear_aplicacion()
    creada.dependency_overrides[dep_repo_reglas] = lambda: repo_reglas
    return creada


def error(respuesta: Any, estado: int, codigo: str) -> dict[str, Any]:
    """Afirma la forma unica del error y devuelve su cuerpo."""
    assert respuesta.status_code == estado, respuesta.text
    cuerpo = respuesta.json()
    assert set(cuerpo) == {"error"}
    assert set(cuerpo["error"]) == {"codigo", "mensaje", "detalles"}
    assert cuerpo["error"]["codigo"] == codigo
    return cuerpo["error"]


def nueva(**cambios: Any) -> dict[str, Any]:
    base = {
        "codigo": "R-13",
        "descripcion": "Monto sobre 5x el promedio del cliente",
        "tipo": "monto",
        "umbral": 5,
        "peso": 10,
    }
    return {**base, **cambios}


# ---------------------------------------------------------------------------
# El doble y la tabla de tipos dicen la verdad
# ---------------------------------------------------------------------------


def prueba_el_doble_cumple_el_protocolo_del_repositorio() -> None:
    assert isinstance(RepoReglasFalso([]), ProtocoloRepoReglas)
    for metodo in ("listar", "obtener", "crear", "editar", "politica_vigente"):
        del_protocolo = inspect.signature(getattr(ProtocoloRepoReglas, metodo))
        assert set(inspect.signature(getattr(RepoReglas, metodo)).parameters) == set(
            del_protocolo.parameters
        )
        assert set(inspect.signature(getattr(RepoReglasFalso, metodo)).parameters) == set(
            del_protocolo.parameters
        )


def prueba_la_tabla_de_tipos_coincide_con_lo_que_lee_el_evaluador() -> None:
    """La tabla es una declaracion; esta prueba la ata al codigo que de verdad lee.

    Si alguien agrega `_parametro(regla, "nuevo")` a un evaluador y no lo declara, la
    API rechazaria una regla valida (o aceptaria una incompleta). Se lee el fuente de
    cada funcion de EVALUADORES y se comparan los nombres.
    """
    assert set(TIPOS_REGLA) == set(EVALUADORES)
    for tipo, funcion in EVALUADORES.items():
        leidos = set(re.findall(r'_parametro\(regla, "(\w+)"\)', inspect.getsource(funcion)))
        declarados = set(TIPOS_REGLA[tipo].parametros)
        if TIPOS_REGLA[tipo].evaluable:
            assert leidos == declarados, tipo
        else:
            assert leidos <= declarados, tipo
    assert evaluador.TIPOS_REGLA["red_mulas"].evaluable is True  # T-63


def prueba_siguiente_regla_id() -> None:
    assert siguiente_regla_id([]) == "REG-001"
    assert siguiente_regla_id(["REG-001", "REG-006", "REG-003"]) == "REG-007"


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_lista_el_catalogo_ordenado_por_codigo(cliente: AsyncClient) -> None:
    respuesta = await cliente.get(RUTA)

    assert respuesta.status_code == status.HTTP_200_OK
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 6
    assert [r["codigo"] for r in cuerpo["elementos"]] == [
        "R-01", "R-03", "R-05", "R-07", "R-09", "R-11",
    ]
    primera = cuerpo["elementos"][0]
    assert primera["_id"] == "REG-001"
    assert "regla_id" not in primera
    assert primera["versiones_anteriores"] == []


@pytest.mark.asyncio
async def prueba_lista_filtra_por_activa(cliente: AsyncClient) -> None:
    cuerpo = (await cliente.get(RUTA, params={"activa": "false"})).json()

    assert [r["codigo"] for r in cuerpo["elementos"]] == ["R-11"]


@pytest.mark.asyncio
async def prueba_obtiene_una_regla(cliente: AsyncClient) -> None:
    respuesta = await cliente.get(f"{RUTA}/REG-005")

    assert respuesta.status_code == status.HTTP_200_OK
    assert respuesta.json()["parametros"] == {"ventana_minutos": 30, "monto_acumulado_crc": 300_000}


@pytest.mark.asyncio
async def prueba_regla_inexistente_es_404(cliente: AsyncClient) -> None:
    cuerpo = error(await cliente.get(f"{RUTA}/REG-099"), 404, "no_encontrado")
    assert "REG-099" in cuerpo["mensaje"]


@pytest.mark.asyncio
async def prueba_identificador_mal_formado_es_422(cliente: AsyncClient) -> None:
    error(await cliente.get(f"{RUTA}/R-01"), 422, "parametros_invalidos")


@pytest.mark.asyncio
async def prueba_publica_los_tipos_soportados(cliente: AsyncClient) -> None:
    respuesta = await cliente.get(f"{RUTA}/tipos")

    assert respuesta.status_code == status.HTTP_200_OK
    tipos = {t["tipo"]: t for t in respuesta.json()["elementos"]}
    assert set(tipos) == set(EVALUADORES)
    assert [p["nombre"] for p in tipos["velocidad"]["parametros"]] == [
        "ventana_minutos",
        "monto_acumulado_crc",
    ]
    assert tipos["monto"]["parametros"] == []
    assert tipos["red_mulas"]["evaluable"] is True  # T-63


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_crea_una_regla_en_version_1(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    respuesta = await cliente.post(RUTA, json=nueva())

    assert respuesta.status_code == status.HTTP_201_CREATED
    creada = respuesta.json()
    assert creada["_id"] == "REG-007"
    assert creada["version"] == 1
    assert creada["esquema_version"] == 1
    assert creada["activa"] is True
    assert creada["versiones_anteriores"] == []
    assert (await cliente.get(f"{RUTA}/REG-007")).json()["codigo"] == "R-13"


@pytest.mark.asyncio
async def prueba_codigo_duplicado_es_409(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    cuerpo = error(await cliente.post(RUTA, json=nueva(codigo="R-01")), 409, "conflicto")

    assert "R-01" in cuerpo["mensaje"]
    assert cuerpo["detalles"][0]["campo"] == "codigo"
    assert repo_reglas.escrituras == 0


@pytest.mark.asyncio
async def prueba_tipo_desconocido_es_422_y_lista_los_soportados(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    cuerpo = error(await cliente.post(RUTA, json=nueva(tipo="geografia")), 422, "parametros_invalidos")

    assert "geografia" in cuerpo["mensaje"]
    for tipo in EVALUADORES:
        assert tipo in cuerpo["mensaje"]
    assert cuerpo["detalles"][0]["campo"] == "tipo"
    assert repo_reglas.escrituras == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cuerpo", "campo"),
    [
        (nueva(tipo="velocidad", umbral=3, parametros={"ventana_minutos": 30}), "parametros.monto_acumulado_crc"),
        (nueva(parametros={"ventana_minutos": 30}), "parametros.ventana_minutos"),
        (
            nueva(
                tipo="velocidad",
                parametros={"ventana_minutos": 30, "monto_acumulado_crc": 300000.5},
            ),
            "parametros.monto_acumulado_crc",
        ),
        (nueva(codigo="R-1"), "codigo"),
        (nueva(peso=101), "peso"),
        (nueva(umbral=-1), "umbral"),
        (nueva(version=4), "version"),
    ],
)
async def prueba_alta_invalida_es_422(
    cliente: AsyncClient, cuerpo: dict[str, Any], campo: str
) -> None:
    detalles = error(await cliente.post(RUTA, json=cuerpo), 422, "parametros_invalidos")["detalles"]

    assert campo in {d["campo"] for d in detalles}


# ---------------------------------------------------------------------------
# Edicion y versionado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_editar_sube_la_version_y_guarda_la_anterior(cliente: AsyncClient) -> None:
    respuesta = await cliente.patch(f"{RUTA}/REG-001", json={"umbral": 20})

    assert respuesta.status_code == status.HTTP_200_OK
    editada = respuesta.json()
    assert editada["version"] == 2
    assert editada["umbral"] == 20
    [anterior] = editada["versiones_anteriores"]
    assert anterior["version"] == 1
    assert anterior["umbral"] == 10
    assert anterior["peso"] == 35
    assert anterior["codigo"] == "R-01"
    assert "reemplazada_en" in anterior

    segunda = (await cliente.patch(f"{RUTA}/REG-001", json={"peso": 30})).json()
    assert segunda["version"] == 3
    assert [v["version"] for v in segunda["versiones_anteriores"]] == [1, 2]
    assert segunda["versiones_anteriores"][1]["umbral"] == 20


@pytest.mark.asyncio
async def prueba_desactivar_es_una_edicion_versionada(cliente: AsyncClient) -> None:
    editada = (await cliente.patch(f"{RUTA}/REG-003", json={"activa": False})).json()

    assert editada["activa"] is False
    assert editada["version"] == 2
    assert editada["versiones_anteriores"][0]["activa"] is True
    activas = (await cliente.get(RUTA, params={"activa": "true"})).json()
    assert "R-05" not in {r["codigo"] for r in activas["elementos"]}


@pytest.mark.asyncio
async def prueba_editar_parametros_los_valida_contra_el_tipo(cliente: AsyncClient) -> None:
    ok = await cliente.patch(
        f"{RUTA}/REG-005", json={"parametros": {"ventana_minutos": 15, "monto_acumulado_crc": 200_000}}
    )
    assert ok.status_code == status.HTTP_200_OK
    assert ok.json()["parametros"]["ventana_minutos"] == 15

    detalles = error(
        await cliente.patch(f"{RUTA}/REG-005", json={"parametros": {"ventana_minuto": 15}}),
        422,
        "parametros_invalidos",
    )["detalles"]
    assert {d["campo"] for d in detalles} >= {
        "parametros.ventana_minuto",
        "parametros.ventana_minutos",
    }


@pytest.mark.asyncio
async def prueba_editar_regla_inexistente_es_404(cliente: AsyncClient) -> None:
    error(await cliente.patch(f"{RUTA}/REG-099", json={"peso": 1}), 404, "no_encontrado")


@pytest.mark.asyncio
async def prueba_version_esperada_vieja_es_409_sin_escribir(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    await cliente.patch(f"{RUTA}/REG-001", json={"umbral": 20})
    escrituras = repo_reglas.escrituras

    cuerpo = error(
        await cliente.patch(f"{RUTA}/REG-001", json={"umbral": 30, "version_esperada": 1}),
        409,
        "conflicto",
    )
    assert cuerpo["detalles"] == [{"campo": "version", "mensaje": "version actual: 2"}]
    assert repo_reglas.escrituras == escrituras


@pytest.mark.asyncio
async def prueba_edicion_concurrente_es_409(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    """Otro supervisor edito entre la lectura y la escritura: el filtro por version
    no encuentra nada y no se pisa su cambio."""
    repo_reglas.interferir_antes_de_editar = True

    error(await cliente.patch(f"{RUTA}/REG-001", json={"umbral": 20}), 409, "conflicto")
    guardada = await repo_reglas.obtener("REG-001")
    assert guardada is not None
    assert guardada["umbral"] == 10
    assert "versiones_anteriores" not in guardada


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"version_esperada": 1},
        {"tipo": "horario"},
        {"codigo": "R-02"},
        {"umbral": None},
        {"peso": -1},
    ],
)
async def prueba_edicion_invalida_es_422(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso, cuerpo: dict[str, Any]
) -> None:
    error(await cliente.patch(f"{RUTA}/REG-001", json=cuerpo), 422, "parametros_invalidos")
    assert repo_reglas.escrituras == 0


@pytest.mark.asyncio
async def prueba_edicion_sin_cambios_reales_no_crea_version(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    respuesta = await cliente.patch(f"{RUTA}/REG-001", json={"umbral": 10, "peso": 35})

    assert respuesta.status_code == status.HTTP_200_OK
    assert respuesta.json()["version"] == 1
    assert repo_reglas.escrituras == 0


# ---------------------------------------------------------------------------
# Simulacion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_simular_con_la_base_da_75_critica(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    respuesta = await cliente.post(f"{RUTA}/simular", json=cuerpo_simulacion_txn_001())

    assert respuesta.status_code == status.HTTP_200_OK, respuesta.text
    evaluacion = respuesta.json()
    assert evaluacion["puntaje"] == 75
    assert evaluacion["severidad"] == "CRITICA"
    assert evaluacion["genera_alerta"] is True
    assert [d["codigo"] for d in evaluacion["reglas_disparadas"]] == ["R-01", "R-03", "R-05"]
    assert all(d["version"] == 1 and d["explicacion"] for d in evaluacion["reglas_disparadas"])
    assert evaluacion["politica_id"] == "POL-001"
    assert repo_reglas.escrituras == 0


@pytest.mark.asyncio
async def prueba_simular_con_umbral_propuesto_baja_el_puntaje(
    cliente: AsyncClient, repo_reglas: RepoReglasFalso
) -> None:
    """Se manda solo R-01 con el umbral nuevo; las otras salen de la base."""
    r01 = {**reglas_como_documentos()[0], "umbral": 20}
    cuerpo = {**cuerpo_simulacion_txn_001(), "reglas": [r01]}

    evaluacion = (await cliente.post(f"{RUTA}/simular", json=cuerpo)).json()

    assert evaluacion["puntaje"] == 40
    assert evaluacion["severidad"] == "MEDIA"
    assert "R-01" not in {d["codigo"] for d in evaluacion["reglas_disparadas"]}
    guardada = await repo_reglas.obtener("REG-001")
    assert guardada is not None and guardada["umbral"] == 10


@pytest.mark.asyncio
async def prueba_simular_con_politica_propuesta(cliente: AsyncClient) -> None:
    politica = politica_como_documento()
    politica["cortes_severidad"] = [
        {"severidad": "MEDIA", "puntaje_minimo": 40, "puntaje_maximo": 79},
        {"severidad": "CRITICA", "puntaje_minimo": 80, "puntaje_maximo": 100},
    ]
    cuerpo = {**cuerpo_simulacion_txn_001(), "politica": politica}

    evaluacion = (await cliente.post(f"{RUTA}/simular", json=cuerpo)).json()

    assert evaluacion["puntaje"] == 75
    assert evaluacion["severidad"] == "MEDIA"


@pytest.mark.asyncio
async def prueba_simular_sin_politica_vigente_es_409(aplicacion: FastAPI, cliente: AsyncClient) -> None:
    aplicacion.dependency_overrides[dep_repo_reglas] = lambda: RepoReglasFalso(
        reglas_como_documentos(), None
    )
    error(await cliente.post(f"{RUTA}/simular", json=cuerpo_simulacion_txn_001()), 409, "conflicto")


@pytest.mark.asyncio
async def prueba_simular_con_contexto_incompleto_es_422(cliente: AsyncClient) -> None:
    cuerpo = cuerpo_simulacion_txn_001()
    del cuerpo["contexto"]["ventana_origen"]

    detalles = error(await cliente.post(f"{RUTA}/simular", json=cuerpo), 422, "parametros_invalidos")["detalles"]
    assert "contexto.ventana_origen" in {d["campo"] for d in detalles}
