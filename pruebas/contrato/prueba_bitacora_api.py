"""T-84: el contrato HTTP de la bitacora de auditoria (`/api/v1/bitacora`), sin Mongo.

El repositorio y el servicio de respaldos se sustituyen por dobles: aqui se sostiene la forma
de la respuesta, los filtros que llegan al repositorio, la paginacion acotada, el CSV y quien
puede llamar (solo el administrador). Que el pipeline devuelva lo correcto contra Mongo lo
prueba `integracion/prueba_bitacora_mongo.py`.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.dependencias import dep_repo_bitacora, dep_servicio_respaldos
from app.modelos.alertas import RolAgente
from app.repos.bitacora import FiltroBitacora, ProtocoloRepoBitacora
from app.respaldos import manifiesto as mf
from app.seguridad.permisos import ACCESOS, Permiso, rol_tiene
from pruebas.sesiones import encabezados_de

RUTA = "/api/v1/bitacora"
T = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _fila(minutos: int, objeto: str = "ALR-000001", actor: str | None = "AGT-001", **campos: Any) -> dict[str, Any]:
    fila = {
        "fecha": T + timedelta(minutes=minutos),
        "actor": actor,
        "accion": "alerta_confirmada",
        "objeto": objeto,
        "detalle": "en_revision -> confirmada: ok",
        "origen": "alertas",
    }
    fila.update(campos)
    return fila


class RepoFalso:
    def __init__(self, filas: list[dict[str, Any]]) -> None:
        self.filas = filas
        self.llamadas: list[tuple[FiltroBitacora, int]] = []

    async def consultar(self, filtro: FiltroBitacora, tope: int) -> tuple[list[dict[str, Any]], int]:
        self.llamadas.append((filtro, tope))
        ordenadas = sorted(self.filas, key=lambda f: f["fecha"], reverse=True)
        return ordenadas[:tope], len(ordenadas)


class ServicioFalso:
    def __init__(self, manifiestos: list[mf.Manifiesto] | None = None) -> None:
        self.manifiestos = manifiestos or []
        self.llamadas = 0

    async def listar(self) -> list[mf.Manifiesto]:
        self.llamadas += 1
        return self.manifiestos


def _manifiesto(minutos: int = 30) -> mf.Manifiesto:
    return mf.Manifiesto(
        respaldo_id="RSP-20261120T123000Z",
        creado_utc=T + timedelta(minutes=minutos),
        creado_por="AGT-003",
        base="antifraude",
        archivo="RSP-20261120T123000Z.gz",
        tamano_bytes=10,
        sha256="c" * 64,
        herramienta="mongodump",
        documentos_por_coleccion={"alertas": 1},
        documentos_al_terminar={"alertas": 1},
    )


@pytest.fixture
def repo(aplicacion: FastAPI) -> RepoFalso:
    falso = RepoFalso([_fila(m, f"ALR-{m:06d}") for m in range(1, 8)])
    aplicacion.dependency_overrides[dep_repo_bitacora] = lambda: falso
    return falso


@pytest.fixture
def servicio(aplicacion: FastAPI) -> ServicioFalso:
    falso = ServicioFalso()
    aplicacion.dependency_overrides[dep_servicio_respaldos] = lambda: falso
    return falso


def admin() -> dict[str, str]:
    return encabezados_de(RolAgente.ADMINISTRADOR)


def prueba_el_doble_cumple_el_protocolo() -> None:
    assert isinstance(RepoFalso([]), ProtocoloRepoBitacora)


# -- Quien puede ------------------------------------------------------------------------------


def prueba_la_bitacora_es_solo_del_administrador() -> None:
    rutas = {(a.metodo, a.ruta): a.permiso for a in ACCESOS if "bitacora" in a.ruta}

    assert rutas == {
        ("GET", "/administracion/bitacora"): Permiso.ADMINISTRACION,
        ("GET", "/api/v1/bitacora"): Permiso.ADMINISTRACION,
        ("GET", "/api/v1/bitacora/exportar.csv"): Permiso.ADMINISTRACION,
    }
    assert rol_tiene(RolAgente.ADMINISTRADOR, Permiso.ADMINISTRACION)
    assert not rol_tiene(RolAgente.SUPERVISOR, Permiso.ADMINISTRACION)
    assert not rol_tiene(RolAgente.AGENTE, Permiso.ADMINISTRACION)


@pytest.mark.asyncio
@pytest.mark.parametrize("rol", [RolAgente.AGENTE, RolAgente.SUPERVISOR], ids=lambda r: r.value)
@pytest.mark.parametrize("ruta", [RUTA, f"{RUTA}/exportar.csv"])
async def prueba_agente_y_supervisor_reciben_403_sin_tocar_el_repositorio(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso, rol: RolAgente, ruta: str
) -> None:
    respuesta = await cliente.get(ruta, headers=encabezados_de(rol))

    assert respuesta.status_code == 403
    assert respuesta.json()["error"]["codigo"] == "prohibido"
    assert repo.llamadas == [] and servicio.llamadas == 0


@pytest.mark.asyncio
async def prueba_sin_sesion_es_401(cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso) -> None:
    respuesta = await cliente.get(RUTA, headers={"Authorization": ""})

    assert respuesta.status_code == 401
    assert repo.llamadas == []


# -- Forma, orden y paginacion ----------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_lista_la_pagina_con_la_forma_documentada(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso
) -> None:
    respuesta = await cliente.get(RUTA, headers=admin())

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert set(cuerpo) == {"total", "limite", "desplazamiento", "elementos"}
    assert cuerpo["total"] == 7 and cuerpo["desplazamiento"] == 0
    assert [e["objeto"] for e in cuerpo["elementos"]] == [f"ALR-{m:06d}" for m in range(7, 0, -1)]
    assert cuerpo["elementos"][0] == {
        "fecha": "2026-11-20T12:07:00Z",
        "actor": "AGT-001",
        "accion": "alerta_confirmada",
        "objeto": "ALR-000007",
        "detalle": "en_revision -> confirmada: ok",
        "origen": "alertas",
    }


@pytest.mark.asyncio
async def prueba_un_actor_nulo_se_publica_como_nulo(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso
) -> None:
    repo.filas = [_fila(1, actor=None, origen="reglas_deteccion", accion="regla_editada")]

    elemento = (await cliente.get(RUTA, headers=admin())).json()["elementos"][0]

    assert elemento["actor"] is None and elemento["origen"] == "reglas_deteccion"


@pytest.mark.asyncio
async def prueba_la_paginacion_pide_al_repositorio_desplazamiento_mas_limite(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso
) -> None:
    primera = (await cliente.get(RUTA, params={"limite": 3}, headers=admin())).json()
    segunda = (await cliente.get(RUTA, params={"limite": 3, "desplazamiento": 3}, headers=admin())).json()
    ultima = (await cliente.get(RUTA, params={"limite": 3, "desplazamiento": 6}, headers=admin())).json()

    assert [tope for _, tope in repo.llamadas] == [3, 6, 9]
    ids = [e["objeto"] for pagina in (primera, segunda, ultima) for e in pagina["elementos"]]
    assert ids == [f"ALR-{m:06d}" for m in range(7, 0, -1)], "sin repetidos ni huecos"
    assert segunda["limite"] == 3 and segunda["desplazamiento"] == 3 and segunda["total"] == 7


@pytest.mark.asyncio
async def prueba_el_limite_se_acota_al_maximo_de_la_aplicacion(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso, ajustes: Any
) -> None:
    cuerpo = (await cliente.get(RUTA, params={"limite": 100_000}, headers=admin())).json()

    assert cuerpo["limite"] <= 500  # app.pagina_maxima; la respuesta dice el que se aplico
    assert repo.llamadas[0][1] == cuerpo["limite"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parametros",
    [{"limite": 0}, {"desplazamiento": -1}, {"desplazamiento": 10_001}],
)
async def prueba_paginacion_sin_sentido_es_422(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso, parametros: dict[str, int]
) -> None:
    respuesta = await cliente.get(RUTA, params=parametros, headers=admin())

    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["codigo"] == "parametros_invalidos"
    assert repo.llamadas == []


# -- Filtros ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_los_filtros_llegan_al_repositorio(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso
) -> None:
    respuesta = await cliente.get(
        RUTA,
        params=[
            ("desde", "2026-11-20T00:00:00-06:00"),
            ("hasta", "2026-11-21T00:00:00Z"),
            ("actor", "AGT-001"),
            ("accion", "alerta_confirmada"),
            ("accion", "regla_editada"),
            ("objeto", "ALR-"),
        ],
        headers=admin(),
    )

    assert respuesta.status_code == 200
    filtro, _ = repo.llamadas[0]
    assert filtro == FiltroBitacora(
        desde=datetime(2026, 11, 20, 6, 0, tzinfo=UTC),
        hasta=datetime(2026, 11, 21, 0, 0, tzinfo=UTC),
        actor="AGT-001",
        acciones=("alerta_confirmada", "regla_editada"),
        objeto="ALR-",
    )


@pytest.mark.asyncio
async def prueba_sin_filtros_el_repositorio_recibe_un_filtro_vacio(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso
) -> None:
    await cliente.get(RUTA, params={"actor": " ", "objeto": ""}, headers=admin())

    assert repo.llamadas[0][0] == FiltroBitacora()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parametros", "campo"),
    [
        ({"desde": "2026-11-21T00:00:00Z", "hasta": "2026-11-20T00:00:00Z"}, "desde"),
        ({"actor": "AGT 1"}, "actor"),
        ({"objeto": "ALR-.*"}, "objeto"),
        ({"accion": "Alerta"}, "accion"),
    ],
)
async def prueba_filtros_incoherentes_son_422_con_su_campo(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso, parametros: dict[str, str], campo: str
) -> None:
    respuesta = await cliente.get(RUTA, params=parametros, headers=admin())

    assert respuesta.status_code == 422
    cuerpo = respuesta.json()["error"]
    assert cuerpo["codigo"] == "parametros_invalidos"
    assert [d["campo"] for d in cuerpo["detalles"]] == [campo]
    assert repo.llamadas == []


# -- Respaldos: la fuente que vive en disco ---------------------------------------------------


@pytest.mark.asyncio
async def prueba_los_respaldos_se_mezclan_con_lo_de_mongo_por_fecha(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso
) -> None:
    servicio.manifiestos = [_manifiesto(minutos=3)]  # mismo minuto que ALR-3: gana el objeto mayor

    cuerpo = (await cliente.get(RUTA, params={"limite": 3, "desplazamiento": 3}, headers=admin())).json()

    assert cuerpo["total"] == 8, "7 de Mongo y 1 de disco"
    assert [e["objeto"] for e in cuerpo["elementos"]] == ["ALR-000004", "RSP-20261120T123000Z", "ALR-000003"]
    primero = cuerpo["elementos"][1]
    assert (primero["origen"], primero["accion"], primero["actor"]) == ("respaldos", "respaldo_creado", "AGT-003")


@pytest.mark.asyncio
async def prueba_los_filtros_tambien_valen_para_los_respaldos(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso
) -> None:
    servicio.manifiestos = [_manifiesto()]

    solo_respaldos = (await cliente.get(RUTA, params={"objeto": "RSP-"}, headers=admin())).json()
    otro_actor = (await cliente.get(RUTA, params={"actor": "AGT-009"}, headers=admin())).json()

    assert [e["objeto"] for e in solo_respaldos["elementos"] if e["origen"] == "respaldos"] == [
        "RSP-20261120T123000Z"
    ]
    assert not [e for e in otro_actor["elementos"] if e["origen"] == "respaldos"]


# -- CSV --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_csv_exporta_lo_filtrado_en_hora_de_costa_rica(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso
) -> None:
    repo.filas = [_fila(1, detalle="=1+1"), _fila(2, actor=None, origen="listas_riesgo")]

    respuesta = await cliente.get(f"{RUTA}/exportar.csv", params={"actor": "AGT-001"}, headers=admin())

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/csv")
    assert 'filename="bitacora.csv"' in respuesta.headers["content-disposition"]
    assert respuesta.headers["x-total-coincidencias"] == "2"
    assert respuesta.headers["x-filas-exportadas"] == "2"
    filas = list(csv.reader(io.StringIO(respuesta.text)))
    assert filas[0] == ["fecha_hora_cr", "actor", "accion", "objeto", "detalle", "origen"]
    assert filas[1][0] == "2026-11-20 06:02:00", "12:02 UTC es 06:02 en Costa Rica"
    assert filas[1][1] == "" and filas[1][5] == "listas_riesgo"
    assert filas[2][4] == "'=1+1", "una celda con formula se neutraliza"
    assert repo.llamadas[0][0].actor == "AGT-001"
    assert repo.llamadas[0][1] == 10_000


@pytest.mark.asyncio
async def prueba_el_csv_rechaza_los_mismos_filtros_incoherentes(
    cliente: AsyncClient, repo: RepoFalso, servicio: ServicioFalso
) -> None:
    respuesta = await cliente.get(f"{RUTA}/exportar.csv", params={"objeto": "x y"}, headers=admin())

    assert respuesta.status_code == 422
