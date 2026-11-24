"""La agrupacion por cuenta destino, su orden y la sugerencia (T-56). Puras: sin Mongo."""

from __future__ import annotations

from datetime import timedelta

from app.api.casos import acciones_del_caso, sugerir_decision
from app.modelos import EstadoAlerta
from app.repos.casos_destino import (
    CasoDestino,
    agrupar_por_destino,
    pipeline_casos,
    reglas_mas_repetidas,
)
from pruebas.dobles.casos_demo import INICIO, MULA, alerta_de_caso, caso_de_la_mula


def _caso(**cambios: object) -> CasoDestino:
    base = {
        "cuenta_destino": MULA, "total_alertas": 3, "victimas": 3, "monto_total_crc": 1,
        "severidad_max": "CRITICA", "puntaje_max": 75, "mas_antigua": INICIO,
        "mas_reciente": INICIO + timedelta(minutes=12), "reglas": [], "alertas": [],
        "en_lista_riesgo": False,
    }
    return CasoDestino(**{**base, **cambios})  # type: ignore[arg-type]


def prueba_agrupa_solo_las_abiertas_por_destino() -> None:
    casos = agrupar_por_destino(caso_de_la_mula())
    assert [c.cuenta_destino for c in casos] == [MULA, "3390-6612"]
    mula = casos[0]
    # La confirmada (ALR-104) no cuenta: el caso es lo que falta por decidir.
    assert mula.total_alertas == 3
    assert mula.victimas == 3
    assert mula.monto_total_crc == 520_000
    assert mula.severidad_max == "CRITICA" and mula.puntaje_max == 75
    assert mula.mas_antigua == INICIO
    assert mula.reglas == [("REG-001", 3), ("REG-002", 1), ("REG-003", 1)]
    assert [a["_id"] for a in mula.alertas] == ["ALR-101", "ALR-103", "ALR-102"]


def prueba_las_victimas_son_clientes_distintos() -> None:
    alertas = [
        alerta_de_caso(1, cliente="Maria"),
        alerta_de_caso(2, cliente="Maria", minuto=2),
        alerta_de_caso(3, cliente="Luis", minuto=3),
    ]
    (caso,) = agrupar_por_destino(alertas)
    assert (caso.total_alertas, caso.victimas) == (3, 2)


def prueba_el_orden_es_severidad_maxima_puntaje_maximo_y_mas_antigua() -> None:
    alertas = [
        alerta_de_caso(1, destino="1111-0001", puntaje=40, minuto=0),
        alerta_de_caso(2, destino="2222-0002", puntaje=75, minuto=30),
        alerta_de_caso(3, destino="3333-0003", puntaje=100, minuto=40),
        alerta_de_caso(4, destino="4444-0004", puntaje=75, minuto=10),
        alerta_de_caso(5, destino="5555-0005", puntaje=60, minuto=0),
    ]
    orden = [c.cuenta_destino for c in agrupar_por_destino(alertas)]
    # Criticas por puntaje (100, luego 75 mas antigua primero), alta y media.
    assert orden == ["3333-0003", "4444-0004", "2222-0002", "5555-0005", "1111-0001"]


def prueba_una_alerta_critica_entre_medias_hace_critico_al_caso() -> None:
    alertas = [alerta_de_caso(1, puntaje=40), alerta_de_caso(2, puntaje=90, minuto=1)]
    (caso,) = agrupar_por_destino(alertas)
    assert caso.severidad_max == "CRITICA" and caso.puntaje_max == 90


def prueba_la_lista_de_alertas_del_caso_se_acota_pero_el_total_no() -> None:
    alertas = [alerta_de_caso(n, minuto=n) for n in range(1, 8)]
    (caso,) = agrupar_por_destino(alertas, maximo_alertas=3)
    assert caso.total_alertas == 7 and len(caso.alertas) == 3 and caso.alertas_ocultas == 4


def prueba_las_reglas_que_mas_se_repiten_desempatan_por_codigo() -> None:
    listas = [["R-02", "R-01"], ["R-02"], ["R-03", "R-01"], ["R-04"]]
    assert reglas_mas_repetidas(listas, limite=3) == [("R-01", 2), ("R-02", 2), ("R-03", 1)]


def prueba_marca_las_cuentas_en_lista_de_riesgo() -> None:
    casos = agrupar_por_destino(caso_de_la_mula(), en_riesgo={MULA})
    assert [c.en_lista_riesgo for c in casos] == [True, False]


def prueba_el_pipeline_solo_mira_alertas_abiertas_y_agrupa_una_vez() -> None:
    etapas = pipeline_casos(limite=10)
    assert etapas[0]["$match"]["estado"]["$in"] == ["en_revision", "nueva"]
    assert sum("$group" in e for e in etapas) == 1
    assert pipeline_casos(cuenta=MULA)[0]["$match"]["cuenta_destino"] == MULA
    assert not any("$facet" in e for e in pipeline_casos(cuenta=MULA))


# --- Sugerencia: pura, solo texto -----------------------------------------------------


def prueba_tres_victimas_en_20_minutos_y_cuenta_reportada_sugiere_confirmar() -> None:
    texto = sugerir_decision(_caso(en_lista_riesgo=True))
    assert texto == "3 víctimas en 12 min y la cuenta ya está reportada: candidata a confirmar."


def prueba_tres_victimas_en_ventana_sin_reporte_sugiere_confirmar_o_escalar() -> None:
    assert "candidata a confirmar o escalar" in (sugerir_decision(_caso()) or "")


def prueba_fuera_de_la_ventana_no_hay_patron() -> None:
    lento = _caso(mas_reciente=INICIO + timedelta(minutes=45))
    assert sugerir_decision(lento) is None


def prueba_pocas_victimas_no_hay_patron_pero_la_cuenta_reportada_se_resalta() -> None:
    assert sugerir_decision(_caso(victimas=1, total_alertas=1)) is None
    assert "ya está reportada" in (sugerir_decision(_caso(victimas=1, en_lista_riesgo=True)) or "")
    assert "2 víctimas" in (sugerir_decision(_caso(victimas=2, en_lista_riesgo=True)) or "")


def prueba_la_sugerencia_no_toca_las_alertas() -> None:
    documentos = caso_de_la_mula()
    antes = [d["estado"] for d in documentos]
    for caso in agrupar_por_destino(documentos, en_riesgo={MULA}):
        sugerir_decision(caso)
    assert [d["estado"] for d in documentos] == antes


def prueba_las_acciones_salen_de_la_maquina_de_estados() -> None:
    nuevas = agrupar_por_destino([alerta_de_caso(1)])[0]
    assert [a[0] for a in acciones_del_caso(nuevas)] == ["en_revision"]
    mezcla = agrupar_por_destino(
        [alerta_de_caso(1), alerta_de_caso(2, estado=EstadoAlerta.EN_REVISION, agente_id="AGT-001")]
    )[0]
    assert [a[0] for a in acciones_del_caso(mezcla)] == [
        "en_revision", "confirmada", "descartada", "escalada",
    ]
