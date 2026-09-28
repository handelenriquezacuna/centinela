"""Contrato de `GET /api/v1/alertas`, con el repositorio sustituido por un doble.

Corre sin Mongo arriba: lo que se verifica aqui es el contrato HTTP (filtros,
paginacion, acotado del limite, forma de la respuesta), no la consulta. La consulta
contra Mongo de verdad se verifica en `prueba_alertas_mongo.py`, marcada `mongo`.
"""

from __future__ import annotations

import pytest
from fastapi import status
from httpx import AsyncClient

from app.repos.alertas import ProtocoloRepoAlertas
from pruebas.dobles.repos_falsos import RepoAlertasFalso

RUTA = "/api/v1/alertas"


def prueba_el_doble_cumple_el_protocolo_del_repositorio() -> None:
    """Si la firma del repositorio real cambia, el doble deja de servir y hay que saberlo.

    `isinstance` contra un Protocol solo comprueba que el metodo exista, asi que
    ademas se comparan las firmas: un parametro nuevo en el repositorio real que el
    doble no tenga haria que las pruebas de contrato prueben algo que ya no existe.
    """
    import inspect

    from app.repos.alertas import RepoAlertas

    assert isinstance(RepoAlertasFalso([]), ProtocoloRepoAlertas)

    del_protocolo = inspect.signature(ProtocoloRepoAlertas.listar)
    del_real = inspect.signature(RepoAlertas.listar)
    del_doble = inspect.signature(RepoAlertasFalso.listar)

    assert set(del_real.parameters) == set(del_protocolo.parameters)
    assert set(del_doble.parameters) == set(del_protocolo.parameters)


@pytest.mark.asyncio
async def prueba_devuelve_las_alertas_del_demo(cliente: AsyncClient) -> None:
    respuesta = await cliente.get(RUTA)

    assert respuesta.status_code == status.HTTP_200_OK
    cuerpo = respuesta.json()

    assert cuerpo["total"] == 3
    assert len(cuerpo["elementos"]) == 3
    assert {alerta["_id"] for alerta in cuerpo["elementos"]} == {
        "ALR-001",
        "ALR-002",
        "ALR-003",
    }


@pytest.mark.asyncio
async def prueba_la_respuesta_trae_total_limite_y_desplazamiento(
    cliente: AsyncClient,
) -> None:
    cuerpo = (await cliente.get(RUTA)).json()

    assert set(cuerpo) == {"total", "limite", "desplazamiento", "elementos"}
    assert cuerpo["desplazamiento"] == 0


@pytest.mark.asyncio
async def prueba_la_alerta_sale_con_el_contrato_de_documento(
    cliente: AsyncClient,
) -> None:
    """`_id`, `esquema_version`, y el historial embebido adentro."""
    cuerpo = (await cliente.get(RUTA, params={"estado": "confirmada"})).json()
    alerta = cuerpo["elementos"][0]

    assert alerta["_id"] == "ALR-001"
    assert "alerta_id" not in alerta
    assert alerta["esquema_version"] == 1
    assert alerta["puntaje"] == 75
    assert alerta["severidad"] == "CRITICA"
    assert alerta["reglas_disparadas"] == ["REG-001", "REG-002", "REG-003"]

    # Historial completo desde el nacimiento: creacion, toma y resolucion.
    assert [entrada["accion"] for entrada in alerta["historial"]] == [
        "nueva",
        "en_revision",
        "confirmada",
    ]
    assert alerta["historial"][0]["agente_id"] is None, "la creacion la hizo el motor"

    # Campos de despliegue: la fila del panel se pinta sin un solo $lookup.
    assert alerta["cuenta_origen"] == "8712-4455"
    assert alerta["cuenta_destino"] == "6033-9001"
    assert alerta["monto_crc"] == 750_000
    assert alerta["moneda"] == "CRC"
    assert alerta["cliente_nombre"] == "Maria Rodriguez Vargas"


@pytest.mark.asyncio
async def prueba_el_puntaje_sale_como_entero_en_el_json(cliente: AsyncClient) -> None:
    import json

    crudo = (await cliente.get(RUTA, params={"estado": "confirmada"})).text
    alerta = json.loads(crudo)["elementos"][0]

    assert isinstance(alerta["puntaje"], int)
    assert "75.0" not in crudo


@pytest.mark.asyncio
async def prueba_la_fecha_sale_en_utc(cliente: AsyncClient) -> None:
    """22:47 de Costa Rica se publica como 04:47Z del dia siguiente."""
    cuerpo = (await cliente.get(RUTA, params={"estado": "confirmada"})).json()

    fecha = cuerpo["elementos"][0]["fecha_creacion"]
    assert fecha.startswith("2026-09-25T04:47")
    assert fecha.endswith("Z") or "+00:00" in fecha


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("estado", "esperados"),
    [
        ("nueva", ["ALR-002"]),
        ("en_revision", ["ALR-003"]),
        ("confirmada", ["ALR-001"]),
        ("descartada", []),
        ("escalada", []),
    ],
)
async def prueba_filtra_por_estado(
    cliente: AsyncClient, estado: str, esperados: list[str]
) -> None:
    cuerpo = (await cliente.get(RUTA, params={"estado": estado})).json()

    assert [alerta["_id"] for alerta in cuerpo["elementos"]] == esperados
    assert cuerpo["total"] == len(esperados)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("severidad", "esperados"),
    [("CRITICA", ["ALR-001"]), ("ALTA", ["ALR-003"]), ("MEDIA", ["ALR-002"])],
)
async def prueba_filtra_por_severidad(
    cliente: AsyncClient, severidad: str, esperados: list[str]
) -> None:
    cuerpo = (await cliente.get(RUTA, params={"severidad": severidad})).json()

    assert [alerta["_id"] for alerta in cuerpo["elementos"]] == esperados


@pytest.mark.asyncio
async def prueba_filtra_por_rango_de_fecha(cliente: AsyncClient) -> None:
    """ALR-002 nace 11:41 y ALR-003 16:06 hora local; en UTC son 17:41 y 22:06."""
    cuerpo = (
        await cliente.get(
            RUTA,
            params={
                "desde": "2026-09-24T17:00:00Z",
                "hasta": "2026-09-24T23:00:00Z",
            },
        )
    ).json()

    assert [alerta["_id"] for alerta in cuerpo["elementos"]] == ["ALR-003", "ALR-002"]
    assert cuerpo["total"] == 2


@pytest.mark.asyncio
async def prueba_el_rango_incluye_los_extremos(cliente: AsyncClient) -> None:
    cuerpo = (
        await cliente.get(
            RUTA,
            params={
                "desde": "2026-09-24T17:41:00Z",
                "hasta": "2026-09-24T17:41:00Z",
            },
        )
    ).json()

    assert [alerta["_id"] for alerta in cuerpo["elementos"]] == ["ALR-002"]


@pytest.mark.asyncio
async def prueba_una_fecha_sin_zona_se_interpreta_como_utc(
    cliente: AsyncClient, repo_alertas_falso: RepoAlertasFalso
) -> None:
    await cliente.get(RUTA, params={"desde": "2026-09-24T17:00:00"})

    recibido = repo_alertas_falso.llamadas[-1]["desde"]
    assert recibido.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def prueba_los_filtros_son_combinables(cliente: AsyncClient) -> None:
    cuerpo = (
        await cliente.get(RUTA, params={"estado": "nueva", "severidad": "CRITICA"})
    ).json()

    assert cuerpo["elementos"] == []
    assert cuerpo["total"] == 0


@pytest.mark.asyncio
async def prueba_un_estado_que_no_existe_es_error_de_validacion(
    cliente: AsyncClient,
) -> None:
    respuesta = await cliente.get(RUTA, params={"estado": "resuelta"})

    assert respuesta.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ---------------------------------------------------------------------------
# Paginacion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_orden_es_lo_mas_reciente_primero(cliente: AsyncClient) -> None:
    cuerpo = (await cliente.get(RUTA)).json()

    assert [alerta["_id"] for alerta in cuerpo["elementos"]] == [
        "ALR-001",
        "ALR-003",
        "ALR-002",
    ]


@pytest.mark.asyncio
async def prueba_el_total_es_del_filtro_y_no_de_la_pagina(cliente: AsyncClient) -> None:
    """Lo que el agente necesita: cuantas alertas hay en su cola, no cuantas ve."""
    cuerpo = (await cliente.get(RUTA, params={"limite": 1})).json()

    assert len(cuerpo["elementos"]) == 1
    assert cuerpo["total"] == 3


@pytest.mark.asyncio
async def prueba_el_desplazamiento_avanza_sin_repetir(cliente: AsyncClient) -> None:
    vistos: list[str] = []
    for desplazamiento in (0, 1, 2):
        cuerpo = (
            await cliente.get(
                RUTA, params={"limite": 1, "desplazamiento": desplazamiento}
            )
        ).json()
        vistos.extend(alerta["_id"] for alerta in cuerpo["elementos"])

    assert vistos == ["ALR-001", "ALR-003", "ALR-002"]
    assert len(set(vistos)) == 3


@pytest.mark.asyncio
async def prueba_un_desplazamiento_pasado_el_final_devuelve_vacio_con_total(
    cliente: AsyncClient,
) -> None:
    cuerpo = (await cliente.get(RUTA, params={"desplazamiento": 99})).json()

    assert cuerpo["elementos"] == []
    assert cuerpo["total"] == 3


@pytest.mark.asyncio
async def prueba_sin_limite_se_usa_el_de_la_configuracion(
    cliente: AsyncClient, repo_alertas_falso: RepoAlertasFalso
) -> None:
    cuerpo = (await cliente.get(RUTA)).json()

    assert cuerpo["limite"] == 50, "app.pagina_por_omision de config/centinela.yml"
    assert repo_alertas_falso.llamadas[-1]["limite"] == 50


@pytest.mark.asyncio
async def prueba_el_limite_se_acota_a_pagina_maxima(
    cliente: AsyncClient, repo_alertas_falso: RepoAlertasFalso
) -> None:
    """El criterio 6 de H-00A: `limite` acotado por `app.pagina_maxima`."""
    cuerpo = (await cliente.get(RUTA, params={"limite": 100_000})).json()

    assert cuerpo["limite"] == 200, "app.pagina_maxima de config/centinela.yml"
    # Y el acotado ocurre ANTES de consultar: el repositorio nunca recibe 100.000.
    assert repo_alertas_falso.llamadas[-1]["limite"] == 200


@pytest.mark.asyncio
async def prueba_el_limite_acotado_se_informa_en_la_respuesta(
    cliente: AsyncClient,
) -> None:
    """El cliente pidio 100.000 y tiene derecho a saber que se le dio 200."""
    cuerpo = (await cliente.get(RUTA, params={"limite": 100_000})).json()

    assert cuerpo["limite"] != 100_000
    assert cuerpo["limite"] == 200


@pytest.mark.asyncio
async def prueba_el_tope_respeta_la_configuracion_y_no_un_numero_del_codigo(
    cliente: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bajar `app.pagina_maxima` por entorno tiene que cambiar el tope."""
    from app.nucleo.config import obtener_ajustes

    monkeypatch.setenv("CENTINELA_APP__PAGINA_MAXIMA", "2")
    monkeypatch.setenv("CENTINELA_APP__PAGINA_POR_OMISION", "1")
    obtener_ajustes.cache_clear()

    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from app.api.dependencias import dep_repo_alertas
    from app.main import crear_aplicacion
    from pruebas.dobles.datos_demo import alertas_como_documentos

    aplicacion = crear_aplicacion()
    doble = RepoAlertasFalso(alertas_como_documentos())
    aplicacion.dependency_overrides[dep_repo_alertas] = lambda: doble

    async with LifespanManager(aplicacion) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as cliente_http:
        sin_limite = (await cliente_http.get(RUTA)).json()
        excesivo = (await cliente_http.get(RUTA, params={"limite": 500})).json()

    assert sin_limite["limite"] == 1
    assert excesivo["limite"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalido", [0, -1])
async def prueba_un_limite_sin_sentido_se_rechaza(
    cliente: AsyncClient, invalido: int
) -> None:
    respuesta = await cliente.get(RUTA, params={"limite": invalido})

    assert respuesta.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def prueba_un_desplazamiento_negativo_se_rechaza(cliente: AsyncClient) -> None:
    respuesta = await cliente.get(RUTA, params={"desplazamiento": -1})

    assert respuesta.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
