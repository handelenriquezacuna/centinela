"""T-84: la parte pura de la bitacora de auditoria, sin Mongo.

El pipeline es un dato: se prueba su FORMA (que cada rama filtre antes de `$unwind`, que no
haya `$lookup`, que las ramas sean las de la tabla de fuentes) y las funciones puras que
normalizan, filtran y mezclan lo que no sale de Mongo (los manifiestos de respaldos). Que el
pipeline devuelva lo correcto lo prueba `integracion/prueba_bitacora_mongo.py` contra Mongo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.api.rutas.bitacora import _a_celda
from app.repos.bitacora import (
    FiltroBitacora,
    coincide,
    construir_pipeline,
    eventos_de_manifiesto,
    fusionar,
)
from app.respaldos import manifiesto as mf

T = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CAMPOS = {"fecha", "actor", "accion", "objeto", "detalle", "origen"}


def _uniones(pipeline: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {e["$unionWith"]["coll"]: e["$unionWith"]["pipeline"] for e in pipeline if "$unionWith" in e}


def _tipos(etapas: list[dict[str, Any]]) -> list[str]:
    return [next(iter(e)) for e in etapas]


def _proyeccion(etapas: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e["$project"] for e in etapas if "$project" in e)


def prueba_el_pipeline_une_las_cinco_colecciones_con_unionwith_y_no_crea_ninguna() -> None:
    pipeline = construir_pipeline(FiltroBitacora(), 20)

    assert sorted(_uniones(pipeline)) == [
        "casos",
        "listas_riesgo",
        "notificaciones",
        "politica_deteccion",
        "reglas_deteccion",
    ]
    assert not any(tipo in ("$out", "$merge", "$lookup") for tipo in _tipos(pipeline))
    assert _tipos(pipeline)[-1] == "$facet"


def prueba_cada_rama_normaliza_a_la_misma_forma() -> None:
    pipeline = construir_pipeline(FiltroBitacora(), 20)
    ramas = [pipeline[: next(i for i, e in enumerate(pipeline) if "$unionWith" in e)]]
    ramas += list(_uniones(pipeline).values())

    assert len(ramas) == 6
    for etapas in ramas:
        assert set(_proyeccion(etapas)) == CAMPOS | {"_id"}


def prueba_con_fecha_actor_y_objeto_cada_rama_poda_antes_del_unwind() -> None:
    filtro = FiltroBitacora(desde=T, hasta=T + timedelta(days=1), actor="AGT-001", objeto="ALR-0")
    pipeline = construir_pipeline(filtro, 20)

    alertas = pipeline[: next(i for i, e in enumerate(pipeline) if "$unionWith" in e)]
    assert _tipos(alertas).index("$match") < _tipos(alertas).index("$unwind")
    poda = alertas[0]["$match"]
    assert poda["_id"] == {"$regex": "^ALR-0"}
    # fecha y agente sobre la MISMA accion del historial, para que lo sirva el indice
    assert poda["historial"] == {"$elemMatch": {"fecha": {"$gte": T, "$lte": T + timedelta(days=1)}, "agente_id": "AGT-001"}}

    for coleccion, etapas in _uniones(pipeline).items():
        primera_proyeccion = _tipos(etapas).index("$project")
        antes = _tipos(etapas).index("$unwind") if "$unwind" in _tipos(etapas) else primera_proyeccion
        assert _tipos(etapas)[0] == "$match", coleccion
        assert antes > 0, coleccion


def prueba_sin_filtros_las_ramas_solo_descartan_lo_que_no_tiene_fecha() -> None:
    uniones = _uniones(construir_pipeline(FiltroBitacora(), 20))

    assert uniones["casos"][0] == {"$match": {"fecha_apertura": {"$type": "date"}}}
    assert uniones["listas_riesgo"][0] == {"$match": {"fecha_reporte": {"$type": "date"}}}
    assert uniones["reglas_deteccion"][0] == {
        "$match": {"versiones_anteriores.reemplazada_en": {"$type": "date"}}
    }
    # notificaciones y alertas no necesitan filtro: todo su contenido cuenta
    assert _tipos(uniones["notificaciones"])[0] == "$project"


def prueba_el_historial_excluye_la_creacion_por_el_motor_y_deja_pasar_acciones_nuevas() -> None:
    alertas = construir_pipeline(FiltroBitacora(), 20)
    despues_del_unwind = next(e["$match"] for e in alertas if "$match" in e and "$or" in e["$match"])

    assert despues_del_unwind["$or"] == [
        {"historial.accion": {"$ne": "nueva"}},
        {"historial.agente_id": {"$ne": None}},
    ]
    # `respuesta_cliente` (T-82) no es un estado: la accion pasa cruda, sin prefijo
    accion = next(e["$project"] for e in alertas if "$project" in e)["accion"]
    assert accion["$cond"][2] == "$historial.accion"


def prueba_el_tipo_de_accion_se_filtra_en_cada_rama() -> None:
    pipeline = construir_pipeline(FiltroBitacora(acciones=("regla_editada",)), 20)
    etapa = {"$match": {"accion": {"$in": ["regla_editada"]}}}

    ramas = [pipeline[:6], *_uniones(pipeline).values()]
    assert all(etapa in etapas for etapas in _uniones(pipeline).values())
    assert etapa in [e for e in pipeline if "$unionWith" not in e]
    assert ramas


def prueba_el_tope_acota_la_pagina_y_el_total_sale_de_la_misma_pasada() -> None:
    facet = construir_pipeline(FiltroBitacora(), 75)[-1]["$facet"]

    assert facet["pagina"][-1] == {"$limit": 75}
    assert facet["total"] == [{"$count": "n"}]
    assert "$sort" in facet["pagina"][0]
    with pytest.raises(ValueError):
        construir_pipeline(FiltroBitacora(), 0)
    with pytest.raises(ValueError):
        construir_pipeline(FiltroBitacora(), 10_101)


@pytest.mark.parametrize(
    ("filtro", "campo"),
    [
        (FiltroBitacora(desde=T + timedelta(days=1), hasta=T), "desde"),
        (FiltroBitacora(actor="AGT 001"), "actor"),
        (FiltroBitacora(objeto="ALR-.*"), "objeto"),
        (FiltroBitacora(acciones=("Alerta",)), "accion"),
    ],
)
def prueba_los_filtros_incoherentes_se_senalan(filtro: FiltroBitacora, campo: str) -> None:
    assert [c for c, _ in filtro.problemas()] == [campo]


def prueba_un_filtro_valido_no_tiene_problemas() -> None:
    filtro = FiltroBitacora(desde=T, hasta=T, actor="AGT-001", acciones=("alerta_confirmada",), objeto="6033-9001")
    assert filtro.problemas() == []


# -- Manifiestos de respaldos ---------------------------------------------------------------


def _manifiesto(verificado: bool = False) -> mf.Manifiesto:
    verificacion = (
        mf.Verificacion(
            verificado_utc=T + timedelta(minutes=5),
            verificado_por="AGT-002",
            resultado="correcta",
            base_temporal="antifraude_verificacion",
            base_temporal_borrada=True,
            duracion_segundos=1.5,
        )
        if verificado
        else None
    )
    return mf.Manifiesto(
        respaldo_id="RSP-20261120T120000Z",
        creado_utc=T,
        creado_por="AGT-001",
        base="antifraude",
        archivo="RSP-20261120T120000Z.gz",
        tamano_bytes=2048,
        sha256="a" * 64,
        herramienta="mongodump version: 100.20.1",
        documentos_por_coleccion={"alertas": 3, "casos": 1},
        documentos_al_terminar={"alertas": 3, "casos": 1},
        verificacion=verificacion,
    )


def prueba_un_respaldo_sin_verificar_es_un_evento() -> None:
    eventos = eventos_de_manifiesto(_manifiesto())

    assert len(eventos) == 1
    assert set(eventos[0]) == CAMPOS
    assert eventos[0]["accion"] == "respaldo_creado"
    assert eventos[0]["actor"] == "AGT-001"
    assert eventos[0]["objeto"] == "RSP-20261120T120000Z"
    assert eventos[0]["origen"] == "respaldos"
    assert "4 documento(s)" in eventos[0]["detalle"]


def prueba_un_respaldo_verificado_son_dos_eventos_con_su_propio_actor() -> None:
    creado, verificado = eventos_de_manifiesto(_manifiesto(verificado=True))

    assert (creado["accion"], creado["actor"], creado["fecha"]) == ("respaldo_creado", "AGT-001", T)
    assert verificado["accion"] == "respaldo_verificado"
    assert verificado["actor"] == "AGT-002"
    assert verificado["fecha"] == T + timedelta(minutes=5)
    assert verificado["detalle"] == "resultado correcta"


def prueba_coincide_aplica_los_mismos_filtros_que_el_pipeline() -> None:
    evento = eventos_de_manifiesto(_manifiesto())[0]

    assert coincide(evento, FiltroBitacora())
    assert coincide(evento, FiltroBitacora(desde=T, hasta=T))  # limites inclusivos
    assert not coincide(evento, FiltroBitacora(desde=T + timedelta(seconds=1)))
    assert not coincide(evento, FiltroBitacora(hasta=T - timedelta(seconds=1)))
    assert coincide(evento, FiltroBitacora(actor="AGT-001"))
    assert not coincide(evento, FiltroBitacora(actor="AGT-002"))
    assert coincide(evento, FiltroBitacora(acciones=("regla_editada", "respaldo_creado")))
    assert not coincide(evento, FiltroBitacora(acciones=("regla_editada",)))
    assert coincide(evento, FiltroBitacora(objeto="RSP-"))
    assert not coincide(evento, FiltroBitacora(objeto="ALR-"))


def _evento(fecha: datetime, objeto: str, actor: str | None = None, accion: str = "a") -> dict[str, Any]:
    return {"fecha": fecha, "actor": actor, "accion": accion, "objeto": objeto, "detalle": "", "origen": "x"}


def prueba_fusionar_ordena_por_fecha_descendente_y_desempata_con_el_resto() -> None:
    a = [_evento(T, "ALR-1"), _evento(T - timedelta(hours=1), "ALR-0")]
    b = [_evento(T, "ALR-2"), _evento(T + timedelta(hours=1), "RSP-1")]

    orden = [e["objeto"] for e in fusionar(a, b)]

    assert orden == ["RSP-1", "ALR-2", "ALR-1", "ALR-0"]  # mismo instante: objeto descendente


def prueba_fusionar_trata_a_las_fechas_ingenuas_como_utc() -> None:
    ingenua = _evento(T.replace(tzinfo=None), "ALR-1")
    con_zona = _evento(T + timedelta(hours=1), "ALR-2")

    resultado = fusionar([ingenua], [con_zona])

    assert [e["objeto"] for e in resultado] == ["ALR-2", "ALR-1"]
    assert resultado[1]["fecha"] == T


def prueba_fusionar_pone_el_actor_nulo_antes_que_los_agentes_en_el_desempate_ascendente() -> None:
    sin_actor = _evento(T, "ALR-1", None)
    con_actor = _evento(T, "ALR-1", "AGT-001")

    assert [e["actor"] for e in fusionar([con_actor], [sin_actor])] == [None, "AGT-001"]


@pytest.mark.parametrize("texto", ["=SUMA(A1)", "+1", "-1", "@x"])
def prueba_el_csv_neutraliza_celdas_que_una_hoja_leeria_como_formula(texto: str) -> None:
    assert _a_celda(texto) == f"'{texto}"
    assert _a_celda("ALR-001: nota") == "ALR-001: nota"
    assert _a_celda("") == ""


def prueba_el_filtro_de_objeto_es_un_prefijo_literal() -> None:
    """Un patron en el filtro se busca como texto: no se interpreta como regex."""
    from app.repos.bitacora import FiltroBitacora, _prefijo

    assert _prefijo(FiltroBitacora(objeto="(a+)+$")) == {"$regex": "^\\(a\\+\\)\\+\\$"}
    assert _prefijo(FiltroBitacora(objeto="ALR-")) == {"$regex": "^ALR-"}
