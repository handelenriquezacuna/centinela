"""H-14 · `planear_transicion`, la parte pura de resolver una alerta. Sin base.

Lo que se fija aqui es la forma de la escritura que el repositorio le pide a Mongo:
historial con `$push` (nunca un `$set` de la lista), filtro condicionado al estado y
al agente leidos, y el caso solo al escalar.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modelos import EstadoAlerta, TransicionInvalida
from app.repos.alertas import ConflictoAlerta, caso_id_para, planear_transicion

AHORA = datetime(2026, 9, 25, 5, 0, tzinfo=UTC)


def _actual(estado: str, agente: str | None = None) -> dict[str, object]:
    documento: dict[str, object] = {"_id": "ALR-002", "estado": estado, "historial": []}
    if agente is not None:
        documento["agente_id"] = agente
    return documento


def prueba_tomar_asigna_agente_y_condiciona_a_que_no_tenga_dueno() -> None:
    plan = planear_transicion(
        _actual("nueva"), destino=EstadoAlerta.EN_REVISION, agente_id="AGT-001", nota="", ahora=AHORA
    )

    assert plan.filtro == {"_id": "ALR-002", "estado": "nueva", "agente_id": {"$exists": False}}
    assert plan.actualizacion["$set"] == {"estado": "en_revision", "agente_id": "AGT-001"}
    assert plan.actualizacion["$push"]["historial"]["accion"] == "en_revision"
    assert "historial" not in plan.actualizacion["$set"], "el historial se agrega, no se pisa"
    assert plan.caso is None


def prueba_resolver_condiciona_al_agente_que_la_tomo() -> None:
    plan = planear_transicion(
        _actual("en_revision", "AGT-001"),
        destino=EstadoAlerta.CONFIRMADA,
        agente_id="AGT-001",
        nota="Cliente confirma llamada previa",
        ahora=AHORA,
    )

    assert plan.filtro == {"_id": "ALR-002", "estado": "en_revision", "agente_id": "AGT-001"}
    assert plan.actualizacion["$set"] == {"estado": "confirmada"}


def prueba_otro_agente_no_resuelve_la_que_no_tomo() -> None:
    with pytest.raises(ConflictoAlerta):
        planear_transicion(
            _actual("en_revision", "AGT-001"),
            destino=EstadoAlerta.DESCARTADA,
            agente_id="AGT-002",
            nota="",
            ahora=AHORA,
        )


def prueba_fuera_de_la_tabla_es_transicion_invalida() -> None:
    with pytest.raises(TransicionInvalida):
        planear_transicion(
            _actual("nueva"), destino=EstadoAlerta.CONFIRMADA, agente_id="AGT-001", nota="", ahora=AHORA
        )


def prueba_escalar_arma_el_caso_derivado_de_la_alerta() -> None:
    plan = planear_transicion(
        _actual("en_revision", "AGT-001"),
        destino=EstadoAlerta.ESCALADA,
        agente_id="AGT-001",
        nota="",
        ahora=AHORA,
    )

    assert plan.caso == {
        "_id": "CAS-002",
        "alertas": ["ALR-002"],
        "estado": "investigacion",
        "fecha_apertura": AHORA,
        "esquema_version": 1,
    }
    assert caso_id_para("ALR-1234") == "CAS-1234"
