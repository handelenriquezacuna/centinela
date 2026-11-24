"""El contrato del canal SSE JSON (T-61), sin Mongo: `GET /api/v1/alertas/eventos`.

Fija lo que la SPA consume: el mismo nombre de evento y el mismo `id` que el canal HTML,
un cuerpo JSON de una linea con `{formato, operacion, alerta}`, la alerta con la forma de
`GET /api/v1/alertas`, y `resincronizar` con `{formato, motivo}` e `id` vacio. La
reanudacion y la resincronizacion son el mismo bucle que el canal HTML
(`eventos_de_alertas`), asi que aqui se verifica que el canal JSON lo use.
"""

from __future__ import annotations

import json

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sse_starlette import ServerSentEvent

from app.api import canal_json
from app.api.canal import EVENTO_ALERTA, EVENTO_RESINCRONIZAR, id_desde_token
from app.api.canal_json import FORMATO_EVENTO, eventos_json
from app.api.dependencias import dep_flujo_alertas
from app.main import crear_aplicacion
from app.modelos import Alerta
from app.repos.flujo_alertas import CambioAlerta
from pruebas.dobles.casos_demo import alerta_de_caso
from pruebas.dobles.flujo_falso import FlujoAlertasFalso, token
from pruebas.sesiones import encabezados_de

RUTA = "/api/v1/alertas/eventos"
REINTENTO_MS = 3000


def cambio(numero: int, *, operacion: str = "insert", **campos: object) -> CambioAlerta:
    documento = {**alerta_de_caso(numero, cliente="María Rodríguez Vargas"), **campos}
    return CambioAlerta(token=token(numero), operacion=operacion, documento=documento)


async def recoger(flujo: FlujoAlertasFalso, **argumentos: object) -> list[ServerSentEvent]:
    return [
        evento
        async for evento in eventos_json(
            flujo,
            id_ultimo_evento=argumentos.get("id_ultimo_evento"),  # type: ignore[arg-type]
            reintento_ms=REINTENTO_MS,
        )
    ]


def _datos(evento: ServerSentEvent) -> dict[str, object]:
    assert isinstance(evento.data, str)
    assert "\n" not in evento.data, "el cuerpo es una sola linea de JSON"
    return json.loads(evento.data)


@pytest.mark.asyncio
async def prueba_la_alerta_sale_en_json_con_el_mismo_nombre_y_el_mismo_id() -> None:
    eventos = await recoger(FlujoAlertasFalso([cambio(101)]))
    apertura, alerta = eventos

    assert apertura.retry == REINTENTO_MS and apertura.data is None
    assert alerta.event == EVENTO_ALERTA == "alerta"
    assert alerta.id == id_desde_token(token(101))
    cuerpo = _datos(alerta)
    assert set(cuerpo) == {"formato", "operacion", "alerta"}
    assert cuerpo["formato"] == FORMATO_EVENTO == 1
    assert cuerpo["operacion"] == "insert"


@pytest.mark.asyncio
async def prueba_la_alerta_tiene_la_forma_de_la_api_y_el_dinero_es_entero() -> None:
    documento = alerta_de_caso(101, monto_crc=280_000)
    (_, evento) = await recoger(
        FlujoAlertasFalso([CambioAlerta(token=token(1), operacion="update", documento=documento)])
    )
    alerta = _datos(evento)["alerta"]

    esperado = Alerta.model_validate(documento).model_dump(mode="json", by_alias=True)
    assert alerta == esperado
    assert alerta["_id"] == "ALR-101"
    assert alerta["monto_crc"] == 280_000 and isinstance(alerta["monto_crc"], int)
    assert alerta["fecha_creacion"].endswith("Z") or "+00:00" in alerta["fecha_creacion"]


@pytest.mark.asyncio
async def prueba_las_tildes_viajan_legibles() -> None:
    (_, evento) = await recoger(FlujoAlertasFalso([cambio(101)]))
    assert "María Rodríguez Vargas" in str(evento.data)


@pytest.mark.asyncio
async def prueba_un_documento_que_no_es_alerta_se_omite_sin_cortar_el_canal() -> None:
    roto = CambioAlerta(token=token(1), operacion="insert", documento={"_id": "ALR-001"})
    eventos = await recoger(FlujoAlertasFalso([roto, cambio(102)]))

    assert [e.event for e in eventos[1:]] == [EVENTO_ALERTA]
    assert _datos(eventos[1])["alerta"]["_id"] == "ALR-102"  # type: ignore[index]


@pytest.mark.asyncio
async def prueba_reanuda_desde_el_token_igual_que_el_canal_html() -> None:
    flujo = FlujoAlertasFalso([cambio(1), cambio(2)])
    eventos = await recoger(flujo, id_ultimo_evento=id_desde_token(token(1)))

    assert flujo.aperturas == [token(1)]
    assert [_datos(e)["alerta"]["_id"] for e in eventos[1:]] == ["ALR-002"]  # type: ignore[index]


@pytest.mark.asyncio
async def prueba_un_token_perdido_resincroniza_en_json_y_borra_el_id() -> None:
    flujo = FlujoAlertasFalso([cambio(1)], rechazar_token=True)
    eventos = await recoger(flujo, id_ultimo_evento=id_desde_token(token(9)))

    resincronizar = eventos[1]
    assert resincronizar.event == EVENTO_RESINCRONIZAR == "resincronizar"
    assert resincronizar.id == ""
    cuerpo = _datos(resincronizar)
    assert set(cuerpo) == {"formato", "motivo"} and cuerpo["formato"] == 1
    assert "ChangeStreamHistoryLost" in str(cuerpo["motivo"])
    # Despues de resincronizar se reabre sin token y siguen llegando alertas en JSON.
    assert flujo.aperturas == [token(9), None]
    assert _datos(eventos[2])["operacion"] == "insert"


@pytest.mark.asyncio
async def prueba_un_id_ilegible_resincroniza_en_json() -> None:
    eventos = await recoger(FlujoAlertasFalso([]), id_ultimo_evento="esto no es base64 %%")
    assert eventos[1].event == EVENTO_RESINCRONIZAR
    assert _datos(eventos[1])["formato"] == 1


def prueba_el_canal_json_no_usa_la_variante_que_serializa_dos_veces() -> None:
    """Se mira lo importado, no el texto: el docstring nombra la clase para explicar por que no."""
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(canal_json))
    importados = {
        alias.name
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.ImportFrom)
        for alias in nodo.names
    }
    assert "JSONServerSentEvent" not in importados
    assert "ServerSentEvent" in importados


@pytest.mark.asyncio
async def prueba_la_ruta_sirve_el_flujo_json() -> None:
    flujo = FlujoAlertasFalso([cambio(101)])
    aplicacion = crear_aplicacion()
    aplicacion.dependency_overrides[dep_flujo_alertas] = lambda: flujo

    async with AsyncClient(
        headers=encabezados_de("agente"),
        transport=ASGITransport(app=aplicacion),
        base_url="http://pruebas",
    ) as cliente:
        respuesta = await cliente.get(RUTA, headers={"Last-Event-ID": id_desde_token(token(100))})

    assert respuesta.status_code == status.HTTP_200_OK
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    assert respuesta.headers["cache-control"] == "no-store"
    assert flujo.aperturas == [token(100)]
    cuerpo = respuesta.text
    assert "event: alerta" in cuerpo and f"id: {id_desde_token(token(101))}" in cuerpo
    linea = next(x for x in cuerpo.splitlines() if x.startswith("data: "))
    assert json.loads(linea.removeprefix("data: "))["alerta"]["_id"] == "ALR-101"


@pytest.mark.asyncio
async def prueba_sin_sesion_el_canal_json_es_401_en_la_forma_del_error() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=crear_aplicacion()), base_url="http://pruebas"
    ) as cliente:
        respuesta = await cliente.get(RUTA)

    assert respuesta.status_code == 401
    assert respuesta.json()["error"]["codigo"] == "no_autenticado"
