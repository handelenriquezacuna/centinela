"""KPI del supervisor (T-51) sin base de datos: las formulas y los casos borde."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from app.api.rutas.paginas_tablero import tiempo_corto
from app.modelos.alertas import RolAgente
from app.repos.indicadores import construir_pipeline, documento_en_ceros
from app.repos.tablero import resumir
from app.seguridad.permisos import permite
from pruebas.dobles.repo_tablero_falso import indicador

AHORA = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _dia(fecha: str, estado: tuple[int, int, int, int, int], **kpi: Any) -> dict[str, Any]:
    return indicador(fecha, sum(estado), (0, 0, sum(estado)), estado, **kpi)


def _kpi(documentos: list[dict[str, Any]], dias: int = 1) -> dict[str, Any]:
    return resumir(documentos, dias, ahora=AHORA)["kpi"]


def prueba_el_tiempo_promedia_ponderando_por_alertas_y_no_promedios_de_promedios() -> None:
    # Dia 1: 2 decisiones, 600 s en total (300 c/u). Dia 2: 1 decision de 900 s.
    docs = [
        _dia("2026-11-18", (0, 0, 2, 0, 0), decision_cantidad=2, decision_segundos_total=600,
             monto_protegido_crc=0, pendientes=0),
        _dia("2026-11-19", (0, 0, 1, 0, 0), decision_cantidad=1, decision_segundos_total=900,
             monto_protegido_crc=0, pendientes=0),
    ]
    # (600 + 900) / 3 = 500; el promedio de promedios daria (300 + 900) / 2 = 600.
    assert _kpi(docs, dias=2)["tiempo_decision_s"]["actual"] == 500


def prueba_sin_alertas_resueltas_no_divide_por_cero() -> None:
    docs = [
        _dia("2026-11-19", (3, 1, 0, 0, 0), decision_cantidad=0, decision_segundos_total=0,
             monto_protegido_crc=0, pendientes=4, pendiente_mas_antigua=datetime(2026, 11, 20, 6, tzinfo=UTC)),
    ]
    kpi = _kpi(docs)

    assert kpi["disponible"] is True
    assert kpi["tiempo_decision_s"]["actual"] is None
    assert kpi["tasa_confirmacion"]["actual"] is None
    assert kpi["monto_protegido_crc"]["actual"] == 0


def prueba_la_tasa_de_confirmacion_ignora_escaladas_y_abiertas() -> None:
    # 3 confirmadas, 1 descartada, 5 escaladas y 7 abiertas: 3 / (3 + 1) = 75.0
    docs = [_dia("2026-11-19", (4, 3, 3, 1, 5), monto_protegido_crc=1, pendientes=7)]
    assert _kpi(docs)["tasa_confirmacion"]["actual"] == 75.0


def prueba_la_tasa_es_cero_si_todo_lo_resuelto_se_descarto() -> None:
    docs = [_dia("2026-11-19", (0, 0, 0, 4, 0), monto_protegido_crc=0, pendientes=0)]
    assert _kpi(docs)["tasa_confirmacion"]["actual"] == 0.0


def prueba_pendientes_suma_y_la_mas_antigua_es_el_minimo() -> None:
    docs = [
        _dia("2026-11-18", (2, 0, 0, 0, 0), monto_protegido_crc=0, pendientes=2,
             pendiente_mas_antigua=datetime(2026, 11, 18, 15, tzinfo=UTC)),
        _dia("2026-11-19", (1, 1, 0, 0, 0), monto_protegido_crc=0, pendientes=2,
             pendiente_mas_antigua=datetime(2026, 11, 19, 9, tzinfo=UTC)),
    ]
    kpi = _kpi(docs, dias=2)

    assert kpi["pendientes"]["actual"] == 4
    assert kpi["pendiente_mas_antigua"] == datetime(2026, 11, 18, 15, tzinfo=UTC)
    assert kpi["antiguedad_horas"] == 45.0  # del 18 15:00 al 20 12:00


def prueba_sin_pendientes_no_hay_mas_antigua() -> None:
    docs = [_dia("2026-11-19", (0, 0, 1, 0, 0), monto_protegido_crc=5, pendientes=0)]
    kpi = _kpi(docs)

    assert kpi["pendientes"]["actual"] == 0
    assert kpi["pendiente_mas_antigua"] is None and kpi["antiguedad_horas"] is None


def prueba_el_monto_protegido_se_compara_con_el_periodo_previo() -> None:
    docs = [
        _dia("2026-11-18", (0, 0, 1, 0, 0), monto_protegido_crc=200_000, pendientes=0),
        _dia("2026-11-19", (0, 0, 2, 0, 1), monto_protegido_crc=300_000, pendientes=0),
    ]
    monto = _kpi(docs)["monto_protegido_crc"]

    assert (monto["actual"], monto["previo"], monto["delta"], monto["porcentaje"]) == (
        300_000, 200_000, 100_000, 50.0,
    )


def prueba_un_rollup_viejo_sin_los_campos_no_inventa_ceros() -> None:
    kpi = _kpi([_dia("2026-11-19", (0, 0, 1, 1, 0))])

    assert kpi["disponible"] is False
    assert kpi["monto_protegido_crc"]["actual"] is None
    assert kpi["pendientes"]["actual"] is None
    assert kpi["tasa_confirmacion"]["actual"] == 50.0  # sale de por_estado, siempre esta


def prueba_si_el_periodo_previo_no_trae_los_campos_no_hay_delta() -> None:
    docs = [
        _dia("2026-11-18", (0, 0, 1, 0, 0)),
        _dia("2026-11-19", (0, 0, 1, 0, 0), monto_protegido_crc=10, pendientes=0,
             decision_cantidad=1, decision_segundos_total=60),
    ]
    kpi = _kpi(docs)

    assert kpi["monto_protegido_crc"]["actual"] == 10
    assert kpi["monto_protegido_crc"]["previo"] is None
    assert kpi["monto_protegido_crc"]["delta"] is None


def prueba_el_pipeline_mide_hasta_la_primera_transicion_terminal_del_historial() -> None:
    etapas = construir_pipeline(date(2026, 11, 22), date(2026, 11, 22))
    agregar = json.dumps(etapas[1]["$addFields"], default=str)

    assert "$historial" in agregar and "$dateDiff" in agregar
    for estado in ("confirmada", "descartada", "escalada"):
        assert estado in agregar
    assert '"nueva"' not in agregar and '"en_revision"' not in agregar
    grupo = etapas[2]["$group"]
    protege = json.dumps(grupo["monto_protegido"], default=str)
    assert "confirmada" in protege and "escalada" in protege and "descartada" not in protege
    pendiente = json.dumps(grupo["pendientes"], default=str)
    assert "nueva" in pendiente and "en_revision" in pendiente


def prueba_el_dia_en_ceros_trae_los_kpi_en_cero_y_sin_mas_antigua() -> None:
    doc = documento_en_ceros("2026-11-22", AHORA)

    assert doc["decision_cantidad"] == doc["decision_segundos_total"] == 0
    assert doc["monto_protegido_crc"] == doc["pendientes"] == 0
    assert "pendiente_mas_antigua" not in doc


def prueba_el_tiempo_se_presenta_corto() -> None:
    assert tiempo_corto(None) == "-"
    assert tiempo_corto(45) == "45 s"
    assert tiempo_corto(90) == "1 min 30 s"
    assert tiempo_corto(3700) == "1 h 1 min"


def prueba_solo_supervisor_y_administrador_gestionan_las_listas() -> None:
    rutas = (
        ("POST", "/api/v1/red-mulas/8712-4455/propuestas/6033-9002"),
        ("POST", "/red-mulas/8712-4455/propuestas/6033-9002"),
        ("DELETE", "/api/v1/listas-riesgo/RSK-001"),
        ("POST", "/red-mulas/8712-4455/listas-riesgo/RSK-001/baja"),
    )
    for metodo, ruta in rutas:
        assert not permite(RolAgente.AGENTE, metodo, ruta), ruta
        assert not permite(None, metodo, ruta), ruta
        assert permite(RolAgente.SUPERVISOR, metodo, ruta), ruta
        assert permite(RolAgente.ADMINISTRADOR, metodo, ruta), ruta
