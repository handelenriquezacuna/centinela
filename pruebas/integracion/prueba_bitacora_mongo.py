"""T-84 contra MongoDB de verdad: la bitacora de auditoria y su procedimiento de mongosh.

Base propia, `antifraude_pruebas_bitacora` (validadores reales, indices reales, mas un relleno de
alertas, notificaciones, listas y casos para medir). Lo que se afirma:

- cada fuente aparece, con su actor, su accion y su detalle (las ediciones de reglas y politica y
  el alta de la lista de riesgo pasan por los repositorios reales);
- cada filtro, solo y combinado, y la paginacion (el total sale de la misma pasada);
- el total y las filas del relleno coinciden con un conteo hecho aparte, a mano, sobre los datos;
- lo que se omite a proposito: la creacion de la alerta por el motor y los casos sin fecha;
- las ramas paralelas (T-82) no rompen nada: una accion de historial que no es un estado y un
  envio por sms pasan tal cual;
- el explain: las consultas con fecha o agente entran por los indices nuevos y la rama de
  notificaciones por el de fecha;
- el procedimiento `scripts/procedimientos/06-bitacora.js` devuelve las MISMAS filas que la
  version de Python, para filtros distintos.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from bson import json_util

from app.nucleo.config import Ajustes
from app.repos.bitacora import FiltroBitacora, RepoBitacora
from pruebas.conftest import RUTA_RAIZ, saltar_o_fallar
from pruebas.integracion.apoyo_bitacora import (
    BASE_BITACORA,
    INDICES_BITACORA,
    INICIO_RELLENO,
    T0,
    alerta,
    preparar_base,
)

pytestmark = pytest.mark.mongo

N_ALERTAS = 1200
PROCEDIMIENTO = RUTA_RAIZ / "scripts" / "procedimientos" / "06-bitacora.js"
INDICES_SEMILLA = RUTA_RAIZ / "scripts" / "semilla" / "02-indices.js"
LAPSO = timedelta(hours=1)


@pytest_asyncio.fixture
async def base_bitacora(base_mongo: Any) -> AsyncIterator[Any]:
    cliente = base_mongo.client
    base = await preparar_base(cliente, N_ALERTAS)
    try:
        yield base
    finally:
        await cliente.drop_database(BASE_BITACORA)


async def _todo(base: Any, filtro: FiltroBitacora, tope: int = 10_000) -> tuple[list[dict[str, Any]], int]:
    return await RepoBitacora(base).consultar(filtro, tope)


def _resumen(filas: list[dict[str, Any]]) -> list[tuple[str, str | None, str, str]]:
    return [(f["origen"], f["actor"], f["accion"], f["objeto"]) for f in filas]


# -- Cada fuente aparece ----------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_cada_fuente_aparece_con_su_actor_su_accion_y_su_detalle(base_bitacora: Any) -> None:
    filas, total = await _todo(base_bitacora, FiltroBitacora(desde=T0 - LAPSO))

    assert total == len(filas) == 11
    assert [f["fecha"] for f in filas] == sorted((f["fecha"] for f in filas), reverse=True)
    # De la mas reciente a la mas antigua; en el mismo instante, el objeto mayor primero.
    assert _resumen(filas) == [
        ("listas_riesgo", None, "lista_riesgo_alta", "6033-9901"),
        ("politica_deteccion", None, "politica_editada", "POL-001"),
        ("reglas_deteccion", None, "regla_editada", "REG-001"),
        ("reglas_deteccion", None, "regla_editada", "REG-001"),
        ("notificaciones", None, "notificacion_fallido", "ALR-900002"),
        ("notificaciones", None, "notificacion_enviado", "ALR-900001"),
        ("casos", "AGT-901", "caso_abierto", "CAS-900001"),
        ("alertas", "AGT-902", "alerta_descartada", "ALR-900002"),
        ("alertas", "AGT-901", "alerta_escalada", "ALR-900001"),
        ("alertas", "AGT-902", "alerta_en_revision", "ALR-900002"),
        ("alertas", "AGT-901", "alerta_en_revision", "ALR-900001"),
    ]
    por_accion = {(f["accion"], f["objeto"]): f["detalle"] for f in filas}
    # Alertas: de que estado a cual, y la nota.
    assert por_accion[("alerta_en_revision", "ALR-900001")] == "nueva -> en_revision: la tomo"
    assert por_accion[("alerta_escalada", "ALR-900001")] == "en_revision -> escalada: a fraude"
    assert por_accion[("alerta_descartada", "ALR-900002")] == "en_revision -> descartada: falso positivo"
    # Reglas: cada par de versiones, con lo que cambio. Primero el par mas reciente.
    reglas = [f["detalle"] for f in filas if f["accion"] == "regla_editada"]
    assert reglas[1].startswith("regla v1 -> v2: ") and "peso 35 -> 30" in reglas[1] and "umbral" in reglas[1]
    assert reglas[0] == "regla v2 -> v3: activa true -> false"
    assert por_accion[("politica_editada", "POL-001")] == "politica v1 -> v2: umbral_alerta 35 -> 40"
    assert por_accion[("lista_riesgo_alta", "6033-9901")].endswith(": cuenta mula reportada")
    assert por_accion[("caso_abierto", "CAS-900001")] == "1 alerta(s): ALR-900001 hacia 6033-9001"
    assert por_accion[("notificacion_enviado", "ALR-900001")].endswith(" por correo")
    assert por_accion[("notificacion_fallido", "ALR-900002")].endswith(" por sms: sin cobertura")
    # Con el cliente configurado como el de la app, las fechas salen con zona.
    assert all(f["fecha"].tzinfo is not None for f in filas)


@pytest.mark.asyncio
async def prueba_se_omiten_la_creacion_por_el_motor_y_los_casos_sin_fecha(base_bitacora: Any) -> None:
    filas, _ = await _todo(base_bitacora, FiltroBitacora(objeto="ALR-9"))
    casos, _ = await _todo(base_bitacora, FiltroBitacora(objeto="CAS-9"))

    assert all(f["actor"] is not None for f in filas if f["origen"] == "alertas")
    assert not [f for f in filas if f["detalle"].startswith("?")]
    assert [f["objeto"] for f in casos] == ["CAS-900001"], "CAS-900002 no trae fecha_apertura"


# -- Filtros ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_los_filtros_solos_y_combinados(base_bitacora: Any) -> None:
    async def consultar(**filtro: Any) -> list[dict[str, Any]]:
        filas, total = await _todo(base_bitacora, FiltroBitacora(**filtro))
        assert total == len(filas)
        return filas

    por_actor = await consultar(actor="AGT-901")
    assert sorted((f["origen"], f["accion"], f["objeto"]) for f in por_actor) == sorted(
        [
            ("alertas", "alerta_en_revision", "ALR-900001"),
            ("alertas", "alerta_escalada", "ALR-900001"),
            ("casos", "caso_abierto", "CAS-900001"),
        ]
    ), "quien no guarda actor (reglas, listas, notificaciones) no aparece al filtrar por agente"

    ediciones = await consultar(acciones=("regla_editada", "politica_editada"))
    assert len(ediciones) == 3
    assert {f["origen"] for f in ediciones} == {"reglas_deteccion", "politica_deteccion"}
    assert [f["objeto"] for f in await consultar(objeto="REG-")] == ["REG-001", "REG-001"]
    assert [f["objeto"] for f in await consultar(objeto="6033-99")] == ["6033-9901"]
    assert len(await consultar(objeto="ALR-9")) == 4 + 2, "cuatro acciones y dos notificaciones"
    assert await consultar(objeto="XYZ-") == []

    # Rango con limites inclusivos: las dos ediciones de la regla, a las +3h y +4h.
    rango = await consultar(desde=T0 + timedelta(hours=3), hasta=T0 + timedelta(hours=4))
    assert [(f["accion"], f["fecha"]) for f in rango] == [
        ("regla_editada", T0 + timedelta(hours=4)),
        ("regla_editada", T0 + timedelta(hours=3)),
    ]
    assert await consultar(desde=T0 + timedelta(days=30)) == []

    combinado = await consultar(actor="AGT-902", acciones=("alerta_descartada",), objeto="ALR-90", desde=T0)
    assert _resumen(combinado) == [("alertas", "AGT-902", "alerta_descartada", "ALR-900002")]


@pytest.mark.asyncio
async def prueba_el_total_y_las_filas_del_relleno_coinciden_con_un_conteo_aparte(base_bitacora: Any) -> None:
    """Sin confiar en el pipeline: se cuentan a mano las acciones del historial y los envios."""
    esperadas = 0
    por_agente = 0
    async for doc in base_bitacora["alertas"].find({"_id": {"$regex": "^ALR-0"}}):
        for accion in doc["historial"]:
            if accion["accion"] == "nueva" and accion.get("agente_id") is None:
                continue
            esperadas += 1
            por_agente += accion.get("agente_id") == "AGT-003"
    envios = await base_bitacora["notificaciones"].count_documents({"alerta_id": {"$regex": "^ALR-0"}})

    _, total = await _todo(base_bitacora, FiltroBitacora(objeto="ALR-0"))
    _, del_agente = await _todo(base_bitacora, FiltroBitacora(actor="AGT-003"))

    assert esperadas > N_ALERTAS and envios == N_ALERTAS // 2
    assert total == esperadas + envios
    assert del_agente == por_agente > 0


@pytest.mark.asyncio
async def prueba_la_paginacion_no_repite_ni_pierde_filas(base_bitacora: Any) -> None:
    filtro = FiltroBitacora(objeto="ALR-0", desde=INICIO_RELLENO + timedelta(days=5))
    todas, total = await _todo(base_bitacora, filtro, 10_000)
    primera, total_primera = await _todo(base_bitacora, filtro, 40)
    segunda, _ = await _todo(base_bitacora, filtro, 80)

    assert total == total_primera == len(todas) > 80
    # Cada tope es un prefijo exacto del siguiente: el orden es total y estable.
    assert primera == todas[:40]
    assert segunda == todas[:80]
    fechas = [f["fecha"] for f in todas]
    assert fechas == sorted(fechas, reverse=True)


# -- Ramas paralelas (T-82 y T-83) ------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_tolera_una_accion_que_no_es_un_estado_y_un_envio_por_sms(base_bitacora: Any) -> None:
    """T-82 escribe `respuesta_cliente` en el historial y notificaciones por `sms`: pasan tal cual."""
    documento = alerta(
        900003,
        [
            {"accion": "nueva", "agente_id": None, "fecha": T0},
            {"accion": "respuesta_cliente", "agente_id": None, "fecha": T0 + timedelta(minutes=9), "nota": "No fui yo"},
        ],
        estado="nueva",
    )
    documento["monto_crc"] = 1
    # El validador de hoy no conoce la accion nueva: se escribe sin el, como quedara cuando
    # T-82 amplie el enum.
    await base_bitacora["alertas"].insert_one(documento, bypass_document_validation=True)
    await base_bitacora["notificaciones"].insert_one(
        {
            "_id": "NOT-900003",
            "alerta_id": "ALR-900003",
            "canal": "sms",
            "estado_envio": "enviado",
            "fecha": T0 + timedelta(minutes=10),
            "esquema_version": 1,
        },
        bypass_document_validation=True,
    )

    filas, _ = await _todo(base_bitacora, FiltroBitacora(objeto="ALR-900003"))

    assert [(f["accion"], f["actor"], f["detalle"]) for f in filas] == [
        ("notificacion_enviado", None, "NOT-900003 por sms"),
        ("respuesta_cliente", None, "No fui yo"),
    ]


@pytest.mark.asyncio
async def prueba_un_agente_con_campos_de_login_no_afecta_a_la_bitacora(base_bitacora: Any) -> None:
    """T-83 agrega intentos fallidos y bloqueo en `agentes`: la bitacora no lee esa coleccion."""
    antes = await _todo(base_bitacora, FiltroBitacora(desde=T0 - LAPSO))
    await base_bitacora["agentes"].insert_one(
        {"_id": "AGT-990", "nombre": "Prueba", "rol": "agente", "activo": True, "esquema_version": 1, "intentos_fallidos": 3},
        bypass_document_validation=True,
    )

    assert await _todo(base_bitacora, FiltroBitacora(desde=T0 - LAPSO)) == antes


# -- Explain ----------------------------------------------------------------------------------


def _indices_de(plan: Any) -> set[str]:
    if isinstance(plan, dict):
        encontrados = {plan["indexName"]} if "indexName" in plan else set()
        for valor in plan.values():
            encontrados |= _indices_de(valor)
        return encontrados
    if isinstance(plan, list):
        encontrados = set()
        for valor in plan:
            encontrados |= _indices_de(valor)
        return encontrados
    return set()


def _estadisticas(explicacion: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Por coleccion (la raiz es `alertas`), los indices del plan ganador y los documentos examinados."""
    etapas = explicacion["stages"]
    cursores = {"alertas": etapas[0]["$cursor"]}
    for etapa in etapas:
        if "$unionWith" in etapa:
            sub = etapa["$unionWith"]
            cursores[sub["coll"]] = sub["pipeline"][0]["$cursor"]
    return {
        coleccion: {
            "indices": _indices_de(cursor["queryPlanner"]["winningPlan"]),
            "examinados": cursor["executionStats"]["totalDocsExamined"],
            "devueltos": cursor["executionStats"]["nReturned"],
        }
        for coleccion, cursor in cursores.items()
    }


@pytest.mark.asyncio
async def prueba_explain_un_dia_entra_por_los_indices_y_no_recorre_las_alertas(base_bitacora: Any) -> None:
    dia = INICIO_RELLENO + timedelta(days=10)
    filtro = FiltroBitacora(desde=dia, hasta=dia + timedelta(days=1))

    estadisticas = _estadisticas(await RepoBitacora(base_bitacora).explicar(filtro, 50))

    assert "idx_alertas_historial_fecha" in estadisticas["alertas"]["indices"]
    assert estadisticas["alertas"]["examinados"] < N_ALERTAS // 5, "no recorre todas las alertas"
    assert "idx_notificaciones_fecha" in estadisticas["notificaciones"]["indices"]
    assert estadisticas["notificaciones"]["examinados"] < N_ALERTAS // 10


@pytest.mark.asyncio
async def prueba_explain_un_agente_entra_por_su_indice(base_bitacora: Any) -> None:
    alertas = _estadisticas(await RepoBitacora(base_bitacora).explicar(FiltroBitacora(actor="AGT-901"), 50))["alertas"]

    assert "idx_alertas_historial_agente_fecha" in alertas["indices"]
    assert alertas["examinados"] == 1


@pytest.mark.asyncio
async def prueba_explain_un_prefijo_de_alerta_entra_por_el_id(base_bitacora: Any) -> None:
    alertas = _estadisticas(await RepoBitacora(base_bitacora).explicar(FiltroBitacora(objeto="ALR-0001"), 50))["alertas"]

    assert alertas["examinados"] <= 100 and alertas["examinados"] == alertas["devueltos"]


def prueba_los_indices_de_la_prueba_son_los_de_02_indices() -> None:
    """La prueba mide con los mismos indices que crea la semilla: si cambian, cambian juntos."""
    texto = INDICES_SEMILLA.read_text(encoding="utf-8")
    for indice in INDICES_BITACORA:
        assert f'name: "{indice["name"]}"' in texto, indice["name"]
        for campo, _ in indice["keys"]:
            assert f'"{campo}"' in texto, campo


# -- El procedimiento de mongosh --------------------------------------------------------------


def _mongosh(ajustes: Ajustes, formato: str = "json", **variables: Any) -> str:
    mongosh = shutil.which("mongosh")
    if mongosh is None:
        saltar_o_fallar("mongosh no esta en el PATH; hace falta para correr los procedimientos")
    asignaciones = [f"BASE_CENTINELA = {json.dumps(BASE_BITACORA)}", f"FORMATO = {json.dumps(formato)}"]
    asignaciones += [f"{nombre} = {json.dumps(valor)}" for nombre, valor in variables.items()]
    resultado = subprocess.run(
        [mongosh, ajustes.mongo.uri, "--quiet", "--eval", "; ".join(asignaciones) + "; void 0", "--file", str(PROCEDIMIENTO)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    return resultado.stdout


def _iso(fecha: datetime) -> str:
    return fecha.astimezone(UTC).isoformat()


def _normalizada(fila: dict[str, Any]) -> tuple[str, str | None, str, str, str, str]:
    fecha = fila["fecha"]
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=UTC)
    return (_iso(fecha), fila["actor"], fila["accion"], fila["objeto"], fila["detalle"], fila["origen"])


CASOS_DE_EQUIVALENCIA = [
    ("sin_filtros", {}, FiltroBitacora(), 100),
    ("un_agente", {"ACTOR": "AGT-901"}, FiltroBitacora(actor="AGT-901"), 100),
    (
        "dos_tipos_de_accion",
        {"ACCION": ["regla_editada", "politica_editada"]},
        FiltroBitacora(acciones=("regla_editada", "politica_editada")),
        100,
    ),
    ("un_prefijo_de_objeto", {"OBJETO": "ALR-9"}, FiltroBitacora(objeto="ALR-9"), 100),
    ("una_cuenta", {"OBJETO": "6033-99"}, FiltroBitacora(objeto="6033-99"), 100),
    (
        "un_rango_de_fechas",
        {"DESDE": _iso(T0 + timedelta(hours=1)), "HASTA": _iso(T0 + timedelta(hours=4))},
        FiltroBitacora(desde=T0 + timedelta(hours=1), hasta=T0 + timedelta(hours=4)),
        100,
    ),
    ("relleno_con_tope", {"OBJETO": "ALR-0"}, FiltroBitacora(objeto="ALR-0"), 37),
    (
        "todo_combinado",
        {"ACTOR": "AGT-902", "ACCION": "alerta_descartada", "OBJETO": "ALR-", "DESDE": _iso(T0)},
        FiltroBitacora(actor="AGT-902", acciones=("alerta_descartada",), objeto="ALR-", desde=T0),
        100,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("variables", "filtro", "tope"),
    [c[1:] for c in CASOS_DE_EQUIVALENCIA],
    ids=[c[0] for c in CASOS_DE_EQUIVALENCIA],
)
async def prueba_el_procedimiento_de_mongosh_devuelve_las_mismas_filas_que_python(
    base_bitacora: Any, ajustes: Ajustes, variables: dict[str, Any], filtro: FiltroBitacora, tope: int
) -> None:
    salida = await asyncio.to_thread(lambda: _mongosh(ajustes, LIMITE=tope, **variables))
    del_script = json_util.loads(salida.strip().splitlines()[-1])
    de_python, total = await _todo(base_bitacora, filtro, tope)

    assert del_script["total"] == total
    assert len(de_python) == min(tope, total)
    assert [_normalizada(f) for f in del_script["filas"]] == [_normalizada(f) for f in de_python]
    assert total > 0, "la comparacion no puede ser trivial"


@pytest.mark.asyncio
async def prueba_el_procedimiento_imprime_una_tabla_y_el_pipeline(base_bitacora: Any, ajustes: Ajustes) -> None:
    tabla = await asyncio.to_thread(lambda: _mongosh(ajustes, "tabla", OBJETO="ALR-9"))
    pipeline = await asyncio.to_thread(lambda: _mongosh(ajustes, "pipeline", OBJETO="ALR-9"))

    assert "Bitacora de auditoria: 6 de 6 fila(s)" in tabla
    assert re.search(r"AGT-901\s+alerta_escalada\s+ALR-900001\s+en_revision -> escalada: a fraude", tabla)
    assert "manifiestos en disco" in tabla
    assert '"$unionWith"' in pipeline and '"coll": "notificaciones"' in pipeline
