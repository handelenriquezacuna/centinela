"""T-33 · La calibracion y lo que quedo sembrado.

Sin base de datos y sin correr la busqueda (que genera datos y tarda): se comprueba que
lo que `scripts/calibracion/calibrar.py` declara como ganadora es lo que dice
`scripts/semilla/03-catalogos.js`, que el caso documentado sigue dando 75 CRITICA con
esa configuracion, y que las metricas de la busqueda usan la definicion de H-10.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

from app.deteccion.evaluador import ContextoEvaluacion, evaluar
from app.modelos import Transaccion
from pruebas.dobles.catalogo_reglas import cuerpo_simulacion_txn_001

RAIZ = Path(__file__).resolve().parents[2]


def _cargar_calibrador():
    ruta = RAIZ / "scripts" / "calibracion" / "calibrar.py"
    especificacion = importlib.util.spec_from_file_location("calibrar_t33", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules["calibrar_t33"] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


calibrar = _cargar_calibrador()
CATALOGO_JS = (RAIZ / "scripts" / "semilla" / "03-catalogos.js").read_text(encoding="utf-8")


def _numero(texto: str) -> float:
    valor = float(texto)
    return int(valor) if valor.is_integer() else valor


def _regla_en_la_semilla(regla_id: str) -> dict[str, float]:
    inicio = CATALOGO_JS.index(f'_id: "{regla_id}"')
    bloque = CATALOGO_JS[inicio : CATALOGO_JS.index("esquema_version", inicio)]
    umbral = re.search(r"^\s+umbral: ([\d.]+),", bloque, re.MULTILINE)
    peso = re.search(r"^\s+peso: (\d+),", bloque, re.MULTILINE)
    activa = re.search(r"^\s+activa: (true|false),", bloque, re.MULTILINE)
    assert umbral and peso and activa, regla_id
    return {
        "umbral": _numero(umbral.group(1)),
        "peso": int(peso.group(1)),
        "activa": activa.group(1) == "true",
    }


def prueba_la_semilla_dice_lo_que_la_calibracion_declara_ganadora() -> None:
    for codigo, regla_id, _, umbral, _, peso, activa in calibrar.GANADORA.reglas:
        sembrada = _regla_en_la_semilla(regla_id)
        assert sembrada == {"umbral": umbral, "peso": peso, "activa": activa}, codigo

    alerta = re.search(r"^\s+umbral_alerta: (\d+),", CATALOGO_JS, re.MULTILINE)
    primer_corte = re.search(
        r'severidad: "MEDIA", puntaje_minimo: (\d+),', CATALOGO_JS, re.MULTILINE
    )
    assert alerta and primer_corte
    assert int(alerta.group(1)) == calibrar.GANADORA.umbral_alerta
    assert int(primer_corte.group(1)) == calibrar.GANADORA.umbral_alerta


def prueba_la_ganadora_no_toca_los_pesos_del_caso_documentado() -> None:
    pesos_base = {fila[0]: fila[5] for fila in calibrar.BASE.reglas}
    pesos_ganadora = {fila[0]: fila[5] for fila in calibrar.GANADORA.reglas}
    assert pesos_ganadora == pesos_base
    assert [pesos_ganadora[c] for c in ("R-01", "R-03", "R-05")] == [35, 25, 15]


def prueba_r11_se_enciende_con_los_parametros_que_la_semilla_ya_declaraba() -> None:
    """T-63: la calibracion de R-11 (`--r11`) elige, entre configuraciones de igual F1, la
    que menos se aparta de la semilla; el cambio aplicado es solo `activa`."""
    base = calibrar.BASE.regla("R-11")
    ganadora = calibrar.GANADORA.regla("R-11")
    assert ganadora[3:6] == base[3:6]
    assert (base[6], ganadora[6]) == (False, True)
    # La rejilla principal no la toca: R-11 sigue apagada en la ganadora de T-33.
    assert calibrar.GANADORA_REJILLA.regla("R-11")[6] is False


@pytest.mark.parametrize("config", [calibrar.BASE, calibrar.GANADORA], ids=lambda c: c.nombre)
def prueba_txn_001_sigue_dando_75_critica(config) -> None:
    cuerpo = cuerpo_simulacion_txn_001()
    evaluacion = evaluar(
        Transaccion.model_validate(cuerpo["transaccion"]),
        ContextoEvaluacion.model_validate(cuerpo["contexto"]),
        calibrar.reglas_de(config),
        calibrar.politica_de(config),
    )

    assert evaluacion.puntaje == 75
    assert evaluacion.severidad == "CRITICA"
    assert [d.codigo for d in evaluacion.reglas_disparadas] == ["R-01", "R-03", "R-05"]


def prueba_las_diferencias_con_la_base_son_exactamente_las_cuatro_aplicadas() -> None:
    assert sorted(calibrar.GANADORA.diferencias(calibrar.BASE)) == [
        "R-01 umbral 10 -> 8",
        "R-05 umbral 0 -> 120",
        "R-11 activa False -> True",
        "umbral_alerta 40 -> 35",
    ]
    # Sin R-11, las tres de T-33.
    assert sorted(calibrar.GANADORA_REJILLA.diferencias(calibrar.BASE)) == [
        "R-01 umbral 10 -> 8",
        "R-05 umbral 0 -> 120",
        "umbral_alerta 40 -> 35",
    ]


def prueba_las_metricas_usan_la_definicion_de_h10() -> None:
    # (fraude, patron, alertada) -> transacciones
    conteos = Counter(
        {
            (True, "cadena_mulas", True): 3,
            (True, "cadena_mulas", False): 1,
            (True, "rafaga_fragmentada", True): 4,
            (False, None, True): 2,
            (False, None, False): 90,
        }
    )
    resultado = calibrar.resultado_de(conteos)

    assert (resultado.detectados, resultado.perdidos, resultado.falsos_positivos) == (7, 1, 2)
    assert resultado.precision == pytest.approx(7 / 9)
    assert resultado.exhaustividad == pytest.approx(7 / 8)
    assert resultado.f1 == pytest.approx(2 * (7 / 9) * (7 / 8) / ((7 / 9) + (7 / 8)))
    assert {p: (f, d) for p, f, d in resultado.por_patron} == {
        "cadena_mulas": (4, 3),
        "rafaga_fragmentada": (4, 4),
    }


def prueba_los_rasgos_de_la_calibracion_son_la_definicion_pura_deslizada() -> None:
    """T-63: `Datos.rasgos` (bisect, rapido) da lo mismo que `calcular_rasgo` sobre todas
    las transacciones del conjunto, que es lo que el pipeline de Mongo reproduce."""
    from datetime import timedelta

    from app.deteccion.rasgos_grafo import calcular_rasgo, indexar

    datos = calibrar.generar_datos(7)
    rasgos = datos.rasgos(15)
    # Todas las del fraude etiquetado (cadenas incluidas) y una muestra del resto.
    muestra = [
        i
        for i, (fraude, _) in enumerate(datos.etiquetas)
        if fraude or i % 97 == 0
    ]
    assert len(muestra) > 60
    for i in muestra:
        txn = datos.transacciones[i]
        desde = txn.fecha - timedelta(hours=calibrar.HORIZONTE_HORAS)
        por_destino, por_origen = indexar(datos.transacciones, desde, txn.fecha)
        esperado = calcular_rasgo(
            txn.cuenta_destino, por_destino, por_origen,
            desde=desde, corte=txn.fecha, ventana_minutos=15, vigencia_segundos=60,
        )  # fmt: skip
        assert rasgos[i] == esperado, txn.transaccion_id
