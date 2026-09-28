"""Construccion del filtro de Mongo para la cola de alertas.

Es una funcion pura, y es donde un error se esconde mejor: un filtro mal armado no
falla, simplemente devuelve el conjunto equivocado con cara de exito.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.modelos import EstadoAlerta, Severidad
from app.repos.alertas import ORDEN_PANEL, construir_filtro

DESDE = datetime(2026, 9, 1, tzinfo=UTC)
HASTA = datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)


def prueba_sin_parametros_el_filtro_esta_vacio() -> None:
    """Un filtro vacio trae todo y deja que el indice del panel haga su trabajo."""
    assert construir_filtro() == {}


def prueba_filtra_por_estado() -> None:
    assert construir_filtro(estado=EstadoAlerta.NUEVA) == {"estado": "nueva"}


def prueba_filtra_por_severidad() -> None:
    assert construir_filtro(severidad=Severidad.CRITICA) == {"severidad": "CRITICA"}


def prueba_guarda_el_valor_y_no_el_enum() -> None:
    """Mongo tiene que recibir texto: un StrEnum sin convertir puede viajar distinto."""
    filtro = construir_filtro(estado=EstadoAlerta.EN_REVISION)

    assert type(filtro["estado"]) is str
    assert filtro["estado"] == "en_revision"


def prueba_acepta_texto_ademas_de_enum() -> None:
    """El estado llega como texto desde la URL."""
    assert construir_filtro(estado="nueva") == {"estado": "nueva"}


def prueba_el_rango_completo_usa_gte_y_lte() -> None:
    assert construir_filtro(desde=DESDE, hasta=HASTA) == {
        "fecha_creacion": {"$gte": DESDE, "$lte": HASTA}
    }


def prueba_solo_desde() -> None:
    assert construir_filtro(desde=DESDE) == {"fecha_creacion": {"$gte": DESDE}}


def prueba_solo_hasta() -> None:
    assert construir_filtro(hasta=HASTA) == {"fecha_creacion": {"$lte": HASTA}}


def prueba_los_filtros_son_combinables() -> None:
    filtro = construir_filtro(
        estado=EstadoAlerta.EN_REVISION,
        severidad=Severidad.ALTA,
        desde=DESDE,
        hasta=HASTA,
    )

    assert filtro == {
        "estado": "en_revision",
        "severidad": "ALTA",
        "fecha_creacion": {"$gte": DESDE, "$lte": HASTA},
    }


def prueba_el_rango_va_sobre_fecha_creacion_y_no_sobre_otra_fecha() -> None:
    """El agente filtra por cuando nacio la alerta, no por cuando se resolvio."""
    assert "fecha_creacion" in construir_filtro(desde=DESDE)


def prueba_el_orden_del_panel_tiene_desempate() -> None:
    """Sin desempate, paginar puede repetir o saltarse un documento."""
    campos = [campo for campo, _ in ORDEN_PANEL]

    assert campos == ["fecha_creacion", "_id"]
    assert ORDEN_PANEL[0][1] == -1, "lo mas reciente primero"
    assert ORDEN_PANEL[1][1] == 1
