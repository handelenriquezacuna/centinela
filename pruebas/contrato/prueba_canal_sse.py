"""El contrato del canal SSE de alertas, sin Mongo.

Estas pruebas fijan la forma del evento, que es lo que el portal (H-17) va a consumir:
nombre del evento, el token de reanudacion como `id`, el cuerpo como HTML sin
serializar, y el evento `resincronizar` cuando el token no sirve.

Lo que estas pruebas NO cubren, dicho para que nadie lo suponga cubierto: la reanudacion
contra un change stream **de verdad**. Aqui se ejercita la traduccion contra un doble
(`pruebas/dobles/flujo_falso.py`), asi que se verifica que el canal reaccione bien a un
token inservible, pero no que Mongo levante `ChangeStreamHistoryLost` cuando el oplog da
la vuelta. El camino feliz -insertar una alerta y verla salir por el canal- si se
comprobo a mano contra el cluster de tres nodos; el de reanudacion no.

Esa prueba de integracion falta y esta anotada como divergencia abierta en
`docs/06-plataforma.md`. Forzar el descarte del oplog pide un replica set con `oplogSize`
diminuto, que es un ambiente aparte del que usa el resto del suite.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sse_starlette import ServerSentEvent

from app.api import canal
from app.api.canal import (
    EVENTO_ALERTA,
    EVENTO_RESINCRONIZAR,
    IdDeEventoInvalido,
    eventos_de_alertas,
    id_desde_token,
    token_desde_id,
)
from app.api.dependencias import dep_flujo_alertas
from app.main import crear_aplicacion
from app.repos.flujo_alertas import CambioAlerta, ProtocoloFlujoAlertas
from pruebas.dobles.flujo_falso import FlujoAlertasFalso, token

RUTA = "/api/v1/alertas/flujo"
REINTENTO_MS = 3000

ALERTA_DEMO = {
    "_id": "ALR-001",
    "transaccion_id": "TXN-001",
    "severidad": "CRITICA",
    "puntaje": 75,
    "estado": "nueva",
    "cuenta_origen": "8712-4455",
    "cuenta_destino": "6033-9001",
    "monto_crc": 750_000,
    "moneda": "CRC",
    "cliente_nombre": "Maria Rodriguez",
    "fecha_creacion": datetime(2026, 9, 25, 4, 47, tzinfo=UTC),
}


def cambio(numero: int, *, operacion: str = "insert", **campos: object) -> CambioAlerta:
    return CambioAlerta(
        token=token(numero),
        operacion=operacion,
        documento={**ALERTA_DEMO, **campos},
    )


async def recoger(flujo: ProtocoloFlujoAlertas, **argumentos: object) -> list[ServerSentEvent]:
    return [
        evento
        async for evento in eventos_de_alertas(
            flujo, reintento_ms=REINTENTO_MS, **argumentos
        )
    ]


# ---------------------------------------------------------------------------
# El token de reanudacion como identificador del evento
# ---------------------------------------------------------------------------


def prueba_el_token_va_y_vuelve_igual() -> None:
    """Ida y vuelta exacta: lo que el navegador devuelve tiene que ser el mismo token.

    Si la codificacion perdiera un campo, la reanudacion fallaria solo en produccion y
    con el oplog lleno, que es el peor lugar para descubrirlo.
    """
    original = {"_data": "826AB9E8AF000000012B042C0100296E5A1004"}

    identificador = id_desde_token(original)

    assert token_desde_id(identificador) == original


def prueba_el_identificador_no_lleva_saltos_de_linea_ni_relleno() -> None:
    """Un salto de linea en el `id` partiria el evento SSE en dos."""
    identificador = id_desde_token({"_data": "82" + "AB" * 40})

    assert "\n" not in identificador and "\r" not in identificador
    assert "=" not in identificador, "base64url sin relleno"


@pytest.mark.parametrize(
    "identificador",
    [
        "no-es-base64-valido-!!",
        "Zm9v",  # base64 correcto de "foo": no es JSON
        "e30",  # base64 de "{}": JSON valido pero token vacio
        "W10",  # base64 de "[]": JSON valido pero no es un objeto
    ],
)
def prueba_un_identificador_que_no_es_token_se_rechaza(identificador: str) -> None:
    """Se rechaza antes de tocar Mongo: el canal responde `resincronizar`."""
    with pytest.raises(IdDeEventoInvalido):
        token_desde_id(identificador)


# ---------------------------------------------------------------------------
# La forma del evento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_apertura_manda_el_reintento_del_navegador() -> None:
    """El primer evento fija el `retry` y empuja bytes para que el canal no parezca colgado."""
    eventos = await recoger(FlujoAlertasFalso([]))

    assert eventos[0].retry == REINTENTO_MS
    assert eventos[0].comment is not None
    assert eventos[0].data is None, "la apertura no lleva cuerpo, solo comentario"


@pytest.mark.asyncio
async def prueba_cada_alerta_sale_con_su_token_como_id() -> None:
    eventos = await recoger(FlujoAlertasFalso([cambio(1), cambio(2)]))

    alertas = [evento for evento in eventos if evento.event == EVENTO_ALERTA]
    assert len(alertas) == 2
    assert [evento.id for evento in alertas] == [
        id_desde_token(token(1)),
        id_desde_token(token(2)),
    ]


@pytest.mark.asyncio
async def prueba_el_cuerpo_es_html_y_no_json() -> None:
    """La trampa del canal, verificada sobre los bytes que salen por el cable.

    El panel inserta el cuerpo en el DOM con HTMX. Si el evento se construyera con
    `JSONServerSentEvent`, el HTML llegaria entre comillas y con las comillas internas
    escapadas, y el agente veria el marcado como texto. Con `ServerSentEvent` sale
    verbatim. Esta prueba lee `encode()` para que la garantia no dependa de recordar
    cual de las dos clases hace que.
    """
    eventos = await recoger(FlujoAlertasFalso([cambio(1)]))
    alerta = next(evento for evento in eventos if evento.event == EVENTO_ALERTA)

    cable = alerta.encode().decode("utf-8")

    assert "data: <div id=\"alerta-ALR-001\"" in cable
    assert '\\"' not in cable, "el HTML llega verbatim, no serializado como JSON"
    assert "data: \"<div" not in cable, "el cuerpo no puede venir entrecomillado"
    assert f"event: {EVENTO_ALERTA}" in cable
    assert "CRITICA" in cable and "750 000" in cable and "Maria Rodriguez" in cable


def prueba_el_canal_no_usa_la_variante_json_del_evento() -> None:
    """Contraprueba de la de arriba, sobre el codigo fuente.

    `sse-starlette` trae dos clases: `ServerSentEvent`, que escribe `data` tal cual, y
    `JSONServerSentEvent`, que lo pasa por `json.dumps`. Para un cuerpo HTML la
    segunda es un error silencioso, asi que el canal no la importa. La prueba mira el
    fuente porque el dia que alguien la use, la falla se ve en el navegador y no en
    ninguna asercion.
    """
    from pathlib import Path

    fuente = Path(canal.__file__).read_text(encoding="utf-8")
    codigo = "\n".join(
        linea for linea in fuente.splitlines() if not linea.strip().startswith("#")
    )

    assert "JSONServerSentEvent(" not in codigo


@pytest.mark.asyncio
async def prueba_el_fragmento_escapa_lo_que_viene_de_los_datos() -> None:
    """`cliente_nombre` es dato que el sistema no controla y se inserta en el DOM.

    Sin escape, un nombre con `<script>` seria una inyeccion en la pantalla del agente.
    """
    eventos = await recoger(
        FlujoAlertasFalso([cambio(1, cliente_nombre="<script>alert(1)</script>")])
    )
    alerta = next(evento for evento in eventos if evento.event == EVENTO_ALERTA)

    assert "<script>" not in str(alerta.data)
    assert "&lt;script&gt;" in str(alerta.data)


@pytest.mark.asyncio
async def prueba_la_operacion_viaja_en_el_fragmento() -> None:
    """El panel agrega una alerta nueva y reemplaza una que cambio de estado.

    Para poder decidir cual de las dos cosas hace, necesita saber si el cambio fue un
    `insert` o un `update`.
    """
    eventos = await recoger(
        FlujoAlertasFalso([cambio(1, operacion="update", estado="en_revision")])
    )
    alerta = next(evento for evento in eventos if evento.event == EVENTO_ALERTA)

    assert 'data-operacion="update"' in str(alerta.data)
    assert 'data-estado="en_revision"' in str(alerta.data)
    assert 'id="alerta-ALR-001"' in str(alerta.data), (
        "el id del elemento permite reemplazar la fila en vez de duplicarla"
    )


# ---------------------------------------------------------------------------
# Reanudacion y resincronizacion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_reanudar_con_un_token_valido_pide_el_flujo_desde_ahi() -> None:
    """Con `Last-Event-ID` valido no se emite `resincronizar`: se sigue donde iba."""
    flujo = FlujoAlertasFalso([cambio(1), cambio(2), cambio(3)])

    eventos = await recoger(flujo, id_ultimo_evento=id_desde_token(token(1)))

    assert flujo.aperturas == [token(1)], "el change stream se abrio con resumeAfter"
    assert not [e for e in eventos if e.event == EVENTO_RESINCRONIZAR]
    assert [e.id for e in eventos if e.event == EVENTO_ALERTA] == [
        id_desde_token(token(2)),
        id_desde_token(token(3)),
    ]


@pytest.mark.asyncio
async def prueba_un_id_ilegible_resincroniza_sin_tocar_la_base() -> None:
    flujo = FlujoAlertasFalso([cambio(9)])

    eventos = await recoger(flujo, id_ultimo_evento="no-es-un-token")

    assert flujo.aperturas == [None], "no se intenta reanudar con un id que no es token"
    nombres = [evento.event for evento in eventos]
    assert nombres == [None, EVENTO_RESINCRONIZAR, EVENTO_ALERTA]


@pytest.mark.asyncio
async def prueba_un_token_que_el_oplog_perdio_resincroniza_y_reabre_sin_token() -> None:
    """El caso `ChangeStreamHistoryLost`, que es el que de verdad pasa en produccion.

    La promesa se cumple con precision: no se reproduce la historia perdida, se le dice
    al panel que vuelva a pedir su cola. Para un panel de trabajo eso es lo correcto;
    el agente quiere su cola como esta ahora, no la repeticion de cada transicion.
    """
    flujo = FlujoAlertasFalso([cambio(5)], rechazar_token=True)

    eventos = await recoger(flujo, id_ultimo_evento=id_desde_token(token(1)))

    assert flujo.aperturas == [token(1), None], (
        "primero se intenta con el token, y tras el rechazo se reabre sin token"
    )
    resincronizaciones = [e for e in eventos if e.event == EVENTO_RESINCRONIZAR]
    assert len(resincronizaciones) == 1
    assert "no sirve" in str(resincronizaciones[0].data)
    assert [e.id for e in eventos if e.event == EVENTO_ALERTA] == [
        id_desde_token(token(5))
    ]


@pytest.mark.asyncio
async def prueba_el_evento_de_resincronizar_borra_el_ultimo_id_del_navegador() -> None:
    """`id:` vacio, y no ausente, para que el navegador suelte el token muerto.

    Sin esto, el `EventSource` insistiria con el mismo `Last-Event-ID` en cada
    reconexion y el panel pediria resincronizar para siempre.
    """
    flujo = FlujoAlertasFalso([], rechazar_token=True)

    eventos = await recoger(flujo, id_ultimo_evento=id_desde_token(token(1)))
    resincronizar = next(e for e in eventos if e.event == EVENTO_RESINCRONIZAR)

    assert resincronizar.id == ""
    assert "id: \r\n" in resincronizar.encode().decode("utf-8")


def prueba_el_doble_cumple_el_protocolo() -> None:
    """Si el repositorio real cambia de firma, el doble deja de servir y hay que saberlo."""
    assert isinstance(FlujoAlertasFalso([]), ProtocoloFlujoAlertas)


# ---------------------------------------------------------------------------
# La ruta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_ruta_sirve_un_flujo_de_eventos() -> None:
    """Camino completo por HTTP, con el repositorio sustituido por el doble.

    El doble termina su flujo, asi que la respuesta cierra sola; con Mongo de verdad la
    conexion queda abierta y es el cliente quien se va.
    """
    aplicacion = crear_aplicacion()
    aplicacion.dependency_overrides[dep_flujo_alertas] = lambda: FlujoAlertasFalso(
        [cambio(1)]
    )

    async with AsyncClient(
        transport=ASGITransport(app=aplicacion), base_url="http://pruebas"
    ) as cliente:
        respuesta = await cliente.get(RUTA)

    assert respuesta.status_code == status.HTTP_200_OK
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    # `no-store` y no `no-cache`: un flujo de eventos no se guarda ni un instante,
    # porque cada evento ya ocurrio y un intermediario que lo reproduzca despues
    # mostraria una cola que no existe.
    assert respuesta.headers["cache-control"] == "no-store"
    cuerpo = respuesta.text
    assert f"event: {EVENTO_ALERTA}" in cuerpo
    assert f"id: {id_desde_token(token(1))}" in cuerpo
    assert 'data: <div id="alerta-ALR-001"' in cuerpo
    assert f"retry: {REINTENTO_MS}" in cuerpo


@pytest.mark.asyncio
async def prueba_la_ruta_acepta_el_ultimo_evento_por_cabecera_y_por_consulta() -> None:
    """La cabecera es lo que manda el navegador; el parametro sirve para `curl`."""
    for nombre, argumentos in (
        ("cabecera", {"headers": {"Last-Event-ID": id_desde_token(token(1))}}),
        ("consulta", {"params": {"ultimo_evento": id_desde_token(token(1))}}),
    ):
        flujo = FlujoAlertasFalso([cambio(1), cambio(2)])
        aplicacion = crear_aplicacion()
        # La sustitucion NO puede llevar parametros: FastAPI inspecciona la firma de
        # lo que se le pasa, y un `lambda flujo=flujo: flujo` le parece un parametro de
        # consulta llamado `flujo`. Se cierra sobre la variable, que no cambia antes de
        # la peticion.
        aplicacion.dependency_overrides[dep_flujo_alertas] = lambda: flujo  # noqa: B023

        async with AsyncClient(
            transport=ASGITransport(app=aplicacion), base_url="http://pruebas"
        ) as cliente:
            respuesta = await cliente.get(RUTA, **argumentos)

        assert flujo.aperturas == [token(1)], f"por {nombre} no se reanudo"
        assert id_desde_token(token(2)) in respuesta.text
