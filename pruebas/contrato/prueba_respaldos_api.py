"""D-74: el contrato HTTP de los respaldos (`/api/v1/respaldos`), sin Mongo ni herramientas.

El servicio se sustituye por un doble: aqui se sostiene la forma de las respuestas, los
codigos y quien puede llamar. Lo que hacen `mongodump` y `mongorestore` de verdad esta en
`pruebas/integracion/prueba_respaldos_mongo.py`.

Quien puede: SOLO el administrador. La tabla `ACCESOS` ya hace que `prueba_permisos.py`
pruebe cada ruta contra cada rol; aqui se agrega la afirmacion explicita de que agente y
supervisor reciben 403 y que no existe una ruta para restaurar.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.dependencias import dep_servicio_respaldos
from app.main import crear_aplicacion
from app.modelos.alertas import RolAgente
from app.respaldos import manifiesto as mf
from app.respaldos.servicio import OperacionEnCurso, RespaldoNoEncontrado
from app.seguridad.permisos import ACCESOS, Permiso, rol_tiene
from pruebas.sesiones import encabezados_de

ID = "RSP-20261124T153012Z"
RUTA = "/api/v1/respaldos"


def _manifiesto(**campos: object) -> mf.Manifiesto:
    base: dict[str, object] = {
        "respaldo_id": ID,
        "creado_utc": datetime(2026, 11, 24, 15, 30, 12, tzinfo=UTC),
        "creado_por": "AGT-001",
        "base": "antifraude",
        "archivo": f"{ID}.gz",
        "tamano_bytes": 2048,
        "sha256": "b" * 64,
        "herramienta": "mongodump version: 100.20.1",
        "documentos_por_coleccion": {"alertas": 3},
        "documentos_al_terminar": {"alertas": 3},
    }
    base.update(campos)
    return mf.Manifiesto(**base)


class ServicioFalso:
    def __init__(self) -> None:
        self.en_curso = False
        self.llamadas: list[tuple[str, ...]] = []
        self.error: Exception | None = None

    async def listar(self) -> list[mf.Manifiesto]:
        return [_manifiesto()]

    async def crear(self, agente_id: str) -> mf.Manifiesto:
        self.llamadas.append(("crear", agente_id))
        if self.error:
            raise self.error
        return _manifiesto(creado_por=agente_id)

    async def verificar(self, respaldo_id: str, agente_id: str) -> mf.Manifiesto:
        self.llamadas.append(("verificar", respaldo_id, agente_id))
        if self.error:
            raise self.error
        return _manifiesto(
            verificacion=mf.Verificacion(
                verificado_utc=datetime(2026, 11, 24, 16, 0, tzinfo=UTC),
                verificado_por=agente_id,
                resultado="correcta",
                base_temporal="antifraude_verificacion",
                base_temporal_borrada=True,
                duracion_segundos=1.5,
                documentos_restaurados={"alertas": 3},
            )
        )


@pytest.fixture
def servicio(aplicacion: FastAPI) -> ServicioFalso:
    falso = ServicioFalso()
    aplicacion.dependency_overrides[dep_servicio_respaldos] = lambda: falso
    return falso


def admin(agente_id: str = "AGT-001") -> dict[str, str]:
    return encabezados_de(RolAgente.ADMINISTRADOR, agente_id)


# ---------------------------------------------------------------------------
# Quien puede
# ---------------------------------------------------------------------------

RUTAS_DE_RESPALDO = [a for a in ACCESOS if "respaldos" in a.ruta]


def prueba_el_permiso_de_respaldos_es_solo_del_administrador() -> None:
    assert rol_tiene(RolAgente.ADMINISTRADOR, Permiso.RESPALDOS)
    assert not rol_tiene(RolAgente.SUPERVISOR, Permiso.RESPALDOS)
    assert not rol_tiene(RolAgente.AGENTE, Permiso.RESPALDOS)


def prueba_toda_ruta_de_respaldos_exige_el_permiso_de_respaldos() -> None:
    assert {(a.metodo, a.ruta) for a in RUTAS_DE_RESPALDO} == {
        ("GET", "/administracion/respaldos"),
        ("GET", "/api/v1/respaldos"),
        ("POST", "/api/v1/respaldos"),
        ("POST", "/api/v1/respaldos/{respaldo_id}/verificar"),
    }
    # la pagina es del area de Administracion; la API de respaldos, del permiso propio
    assert {a.permiso for a in RUTAS_DE_RESPALDO if a.ruta.startswith("/api/")} == {Permiso.RESPALDOS}
    assert [a.permiso for a in RUTAS_DE_RESPALDO if a.ruta.startswith("/administracion")] == [
        Permiso.ADMINISTRACION
    ]


def prueba_no_hay_ruta_para_restaurar_sobre_la_base_viva() -> None:
    """Restaurar reemplaza datos reales: es `python tareas.py restaurar`, nunca HTTP."""
    publicadas = {ruta.path for ruta in crear_aplicacion().routes if hasattr(ruta, "path")}

    assert not [r for r in publicadas if "restaur" in r.lower()]
    assert not [a for a in ACCESOS if "restaur" in a.ruta.lower()]


@pytest.mark.asyncio
@pytest.mark.parametrize("rol", [RolAgente.AGENTE, RolAgente.SUPERVISOR], ids=lambda r: r.value)
@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [("GET", RUTA), ("POST", RUTA), ("POST", f"{RUTA}/{ID}/verificar")],
)
async def prueba_agente_y_supervisor_reciben_403(
    cliente: AsyncClient, servicio: ServicioFalso, rol: RolAgente, metodo: str, ruta: str
) -> None:
    respuesta = await cliente.request(metodo, ruta, headers=encabezados_de(rol))

    assert respuesta.status_code == 403
    assert servicio.llamadas == [], "el servicio ni se entero"
    if ruta.startswith("/api/"):
        assert respuesta.json()["error"]["codigo"] == "prohibido"


@pytest.mark.asyncio
async def prueba_sin_sesion_la_api_de_respaldos_da_401(
    aplicacion: FastAPI, servicio: ServicioFalso
) -> None:
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as anonimo:
        assert (await anonimo.get(RUTA)).status_code == 401
        assert (await anonimo.post(RUTA)).status_code == 401


# ---------------------------------------------------------------------------
# Lo que responde al administrador
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_lista_trae_los_manifiestos_el_total_y_si_hay_una_operacion_en_curso(
    cliente: AsyncClient, servicio: ServicioFalso
) -> None:
    servicio.en_curso = True

    respuesta = await cliente.get(RUTA, headers=admin())

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 1 and cuerpo["en_curso"] is True
    elemento = cuerpo["elementos"][0]
    assert elemento["respaldo_id"] == ID
    assert elemento["creado_utc"] == "2026-11-24T15:30:12Z"
    assert elemento["documentos_por_coleccion"] == {"alertas": 3}
    assert elemento["verificacion"] is None


@pytest.mark.asyncio
async def prueba_crear_responde_201_y_el_autor_es_la_sesion(
    cliente: AsyncClient, servicio: ServicioFalso
) -> None:
    respuesta = await cliente.post(RUTA, headers=admin("AGT-777"))

    assert respuesta.status_code == 201
    assert respuesta.json()["creado_por"] == "AGT-777"
    assert servicio.llamadas == [("crear", "AGT-777")]


@pytest.mark.asyncio
async def prueba_verificar_responde_200_con_el_resultado_dentro_del_manifiesto(
    cliente: AsyncClient, servicio: ServicioFalso
) -> None:
    respuesta = await cliente.post(f"{RUTA}/{ID}/verificar", headers=admin("AGT-778"))

    assert respuesta.status_code == 200
    verificacion = respuesta.json()["verificacion"]
    assert verificacion["resultado"] == "correcta"
    assert verificacion["verificado_por"] == "AGT-778"
    assert verificacion["base_temporal_borrada"] is True
    assert servicio.llamadas == [("verificar", ID, "AGT-778")]


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", [RUTA, f"{RUTA}/{ID}/verificar"])
async def prueba_otra_operacion_en_curso_es_409_con_la_forma_unica_del_error(
    cliente: AsyncClient, servicio: ServicioFalso, ruta: str
) -> None:
    servicio.error = OperacionEnCurso("ya hay un respaldo o una verificacion en curso")

    respuesta = await cliente.post(ruta, headers=admin())

    assert respuesta.status_code == 409
    assert respuesta.json()["error"]["codigo"] == "conflicto"
    assert set(respuesta.json()["error"]) == {"codigo", "mensaje", "detalles"}


@pytest.mark.asyncio
async def prueba_verificar_un_respaldo_que_no_existe_es_404(
    cliente: AsyncClient, servicio: ServicioFalso
) -> None:
    servicio.error = RespaldoNoEncontrado("no existe el respaldo")

    respuesta = await cliente.post(f"{RUTA}/{ID}/verificar", headers=admin())

    assert respuesta.status_code == 404
    assert respuesta.json()["error"]["codigo"] == "no_encontrado"


@pytest.mark.asyncio
@pytest.mark.parametrize("identificador", ["RSP-1", "x", "..%2F..%2Fetc", "RSP-20261124T153012Z.gz"])
async def prueba_un_id_con_mala_forma_es_422_y_no_llega_al_servicio(
    cliente: AsyncClient, servicio: ServicioFalso, identificador: str
) -> None:
    respuesta = await cliente.post(f"{RUTA}/{identificador}/verificar", headers=admin())

    assert respuesta.status_code in (404, 422)
    assert servicio.llamadas == []


@pytest.mark.asyncio
async def prueba_una_herramienta_que_falla_es_500_con_la_forma_unica(
    cliente: AsyncClient, servicio: ServicioFalso, caplog: pytest.LogCaptureFixture
) -> None:
    from app.respaldos.servicio import HerramientaFallida

    servicio.error = HerramientaFallida("mongodump termino con codigo 1")

    respuesta = await cliente.post(RUTA, headers=admin())

    assert respuesta.status_code == 500
    assert respuesta.json()["error"]["codigo"] == "error_interno"
    assert "mongodump" in respuesta.json()["error"]["mensaje"]


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

ESQUEMA = crear_aplicacion().openapi()


def prueba_openapi_publica_los_tres_endpoints_con_sus_codigos() -> None:
    lista = ESQUEMA["paths"][RUTA]
    verificar = ESQUEMA["paths"][f"{RUTA}/{{respaldo_id}}/verificar"]

    assert set(lista) == {"get", "post"}
    assert set(verificar) == {"post"}
    assert set(lista["get"]["responses"]) >= {"200", "401", "403", "500"}
    assert set(lista["post"]["responses"]) >= {"201", "401", "403", "409", "500"}
    assert set(verificar["post"]["responses"]) >= {"200", "404", "409", "422"}


def prueba_openapi_publica_el_manifiesto_con_sus_campos() -> None:
    esquema = ESQUEMA["components"]["schemas"]["Manifiesto"]

    assert set(esquema["properties"]) >= {
        "respaldo_id", "creado_utc", "creado_por", "base", "archivo", "tamano_bytes",
        "sha256", "documentos_por_coleccion", "documentos_al_terminar", "verificacion",
        "esquema_version",
    }
    assert esquema["properties"]["tamano_bytes"]["type"] == "integer"
    assert "Verificacion" in ESQUEMA["components"]["schemas"]


def prueba_el_409_y_el_404_publican_el_modelo_de_error_de_centinela() -> None:
    for ruta, estado in ((RUTA, "409"), (f"{RUTA}/{{respaldo_id}}/verificar", "404")):
        respuesta = ESQUEMA["paths"][ruta]["post"]["responses"][estado]
        referencia = respuesta["content"]["application/json"]["schema"]["$ref"]
        assert referencia.endswith("RespuestaError")


def prueba_el_id_de_la_ruta_publica_su_patron() -> None:
    parametro = ESQUEMA["paths"][f"{RUTA}/{{respaldo_id}}/verificar"]["post"]["parameters"][0]

    assert parametro["schema"]["pattern"] == mf.PATRON_ID


def prueba_la_auditoria_tiene_su_logger() -> None:
    """Todo queda en `centinela.auditoria`: el logger existe en el servicio con ese nombre."""
    from app.respaldos import servicio

    assert servicio.auditoria.name == "centinela.auditoria"
    assert isinstance(servicio.auditoria, logging.Logger)
