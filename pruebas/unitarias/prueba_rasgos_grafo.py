"""T-63 sin base de datos: la definicion de los rasgos, R-11 contra el rasgo y D3.

El caso de la demo es el de siempre: 6033-9001 recibe 280.000 (TXN-001, 22:47) y reenvia
276.000 cuatro minutos despues (TXN-005), y la cadena sigue dos saltos mas (TXN-006 y
TXN-007). Sale de `pruebas/dobles/datos_demo.py`, no de numeros escritos aca.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from app.deteccion import RasgoRed
from app.deteccion.d3 import d3
from app.deteccion.rasgos_grafo import (
    CLASE_RASGO,
    NOMBRE_RASGOS,
    SALTOS_MAXIMOS,
    calcular_rasgo,
    id_rasgo,
    indexar,
    leer_argumentos,
)
from app.modelos.transaccional import Transaccion
from app.nucleo.config import Ajustes, AjustesIndicadores, AjustesRasgosGrafo
from app.repos.rasgos_grafo import a_rasgo_red, construir_pipeline
from app.supervision import Supervisor
from pruebas.dobles.datos_demo import TRANSACCIONES
from pruebas.unitarias.prueba_evaluador import contexto, regla, transaccion, una

MULA = "6033-9001"
CORTE = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)  # 24-sep 24:00 en Costa Rica
DESDE = CORTE - timedelta(hours=24)


def rasgo_demo(**cambios: object) -> RasgoRed:
    """El rasgo de 6033-9001 calculado con la definicion pura sobre el conjunto de demo."""
    por_destino, por_origen = indexar(TRANSACCIONES, DESDE, CORTE)
    rasgo = calcular_rasgo(
        MULA,
        por_destino,
        por_origen,
        desde=DESDE,
        corte=CORTE,
        ventana_minutos=15,
        vigencia_segundos=60,
    )
    return rasgo.model_copy(update=cambios)


def _txn(n: int, origen: str, destino: str, monto: int, minuto: int) -> Transaccion:
    return Transaccion.model_validate(
        {
            "_id": f"TXN-{n:03d}",
            "cuenta_origen": f"CTA-{n:03d}",
            "sinpe_origen": origen,
            "cuenta_destino": destino,
            "monto_crc": monto,
            "moneda": "CRC",
            "canal": "sinpe_movil",
            "fecha": DESDE + timedelta(minutes=minuto),
            "esquema_version": 1,
        }
    )


# ---------------------------------------------------------------------------
# La definicion
# ---------------------------------------------------------------------------


def prueba_el_rasgo_de_la_mula_de_la_demo() -> None:
    rasgo = rasgo_demo()

    assert rasgo.monto_recibido_crc == 280_000
    assert rasgo.monto_reenviado_crc == 276_000
    # 276.000 / 280.000 = 98,57 %: en puntos basicos enteros, 9857 (98,6 %).
    assert rasgo.proporcion_pb == 9857
    assert rasgo.minutos_reenvio == 4
    assert (rasgo.fan_in, rasgo.fan_out) == (1, 1)
    # 6033-9001 -> 7104-2288 -> 8455-1177 -> 6001-3344: tres saltos.
    assert rasgo.saltos == 3


def prueba_el_ultimo_eslabon_no_reenvia_nada() -> None:
    por_destino, por_origen = indexar(TRANSACCIONES, DESDE, CORTE)
    rasgo = calcular_rasgo(
        "6001-3344",
        por_destino,
        por_origen,
        desde=DESDE,
        corte=CORTE,
        ventana_minutos=15,
        vigencia_segundos=60,
    )

    assert (rasgo.monto_recibido_crc, rasgo.monto_reenviado_crc) == (90_000, 0)
    assert (rasgo.proporcion_pb, rasgo.saltos, rasgo.fan_out) == (0, 0, 0)


def prueba_una_cuenta_sin_movimiento_es_un_rasgo_en_ceros() -> None:
    por_destino, por_origen = indexar(TRANSACCIONES, DESDE, CORTE)
    rasgo = calcular_rasgo(
        "0000-0000",
        por_destino,
        por_origen,
        desde=DESDE,
        corte=CORTE,
        ventana_minutos=15,
        vigencia_segundos=60,
    )

    assert rasgo.monto_recibido_crc == 0
    assert rasgo.proporcion_pb == 0


def prueba_lo_reenviado_fuera_de_la_ventana_no_cuenta() -> None:
    # Recibe a los 0 min, reenvia a los 20: con ventana de 15 no es reenvio rapido.
    txns = [_txn(1, "1111-1111", "2222-2222", 100_000, 0), _txn(2, "2222-2222", "3333-3333", 100_000, 20)]
    por_destino, por_origen = indexar(txns, DESDE, CORTE)

    def con(ventana: int) -> RasgoRed:
        return calcular_rasgo(
            "2222-2222",
            por_destino,
            por_origen,
            desde=DESDE,
            corte=CORTE,
            ventana_minutos=ventana,
            vigencia_segundos=60,
        )

    assert con(15).monto_reenviado_crc == 0
    assert con(15).saltos == 1, "el recorrido de la cadena no depende de la ventana"
    assert con(20).monto_reenviado_crc == 100_000
    assert con(20).minutos_reenvio == 20


def prueba_no_se_reenvia_mas_de_lo_que_entro() -> None:
    txns = [
        _txn(1, "1111-1111", "2222-2222", 50_000, 0),
        _txn(2, "2222-2222", "3333-3333", 40_000, 1),
        _txn(3, "2222-2222", "4444-4444", 40_000, 2),
    ]
    por_destino, por_origen = indexar(txns, DESDE, CORTE)
    rasgo = calcular_rasgo(
        "2222-2222",
        por_destino,
        por_origen,
        desde=DESDE,
        corte=CORTE,
        ventana_minutos=15,
        vigencia_segundos=60,
    )

    assert rasgo.monto_reenviado_crc == 50_000
    assert rasgo.proporcion_pb == 10_000
    assert rasgo.fan_out == 2


def prueba_el_recorrido_de_la_cadena_termina_con_un_ciclo_y_con_el_tope() -> None:
    ciclo = [_txn(1, "1111-1111", "2222-2222", 10_000, 0), _txn(2, "2222-2222", "1111-1111", 10_000, 1)]
    por_destino, por_origen = indexar(ciclo, DESDE, CORTE)
    rasgo = calcular_rasgo(
        "1111-1111", por_destino, por_origen,
        desde=DESDE, corte=CORTE, ventana_minutos=15, vigencia_segundos=60,
    )  # fmt: skip
    assert rasgo.saltos == 2

    largo = [
        _txn(n, f"{n:04d}-0000", f"{n + 1:04d}-0000", 10_000, n) for n in range(1, SALTOS_MAXIMOS + 4)
    ]
    por_destino, por_origen = indexar(largo, DESDE, CORTE)
    rasgo = calcular_rasgo(
        "0002-0000", por_destino, por_origen,
        desde=DESDE, corte=CORTE, ventana_minutos=15, vigencia_segundos=60,
    )  # fmt: skip
    assert rasgo.saltos == SALTOS_MAXIMOS


def prueba_el_rango_es_cerrado_por_la_izquierda_y_abierto_por_la_derecha() -> None:
    en_corte = _txn(1, "1111-1111", "2222-2222", 10_000, 0).model_copy(update={"fecha": CORTE})
    en_desde = _txn(2, "1111-1111", "2222-2222", 20_000, 0).model_copy(update={"fecha": DESDE})
    por_destino, _ = indexar([en_corte, en_desde], DESDE, CORTE)

    assert [t.monto_crc for t in por_destino["2222-2222"]] == [20_000]


# ---------------------------------------------------------------------------
# R-11 contra el rasgo
# ---------------------------------------------------------------------------


def _ctx(rasgo: RasgoRed | None):
    return contexto(rasgo_red=rasgo)


def _siguiente(minutos: int = 1):
    """La transaccion siguiente hacia la mula, `minutos` despues del corte."""
    return transaccion(fecha=CORTE + timedelta(minutes=minutos))


def prueba_r11_dispara_con_el_caso_de_la_demo_y_explica_en_colones_y_minutos() -> None:
    evaluacion = una("R-11", tx=_siguiente(), ctx=_ctx(rasgo_demo()), activa=True)

    [disparada] = evaluacion.reglas_disparadas
    assert disparada.codigo == "R-11"
    assert disparada.peso == 20
    assert "recibio 280.000 CRC" in disparada.explicacion
    assert "reenvio 276.000 CRC (98,6 %)" in disparada.explicacion
    assert "en 4 minutos" in disparada.explicacion
    assert "3 saltos" in disparada.explicacion
    assert "umbral 80 %" in disparada.explicacion
    assert evaluacion.reglas_no_evaluadas == ()


def prueba_r11_compara_la_fraccion_exacta_no_el_flotante() -> None:
    # 0,8 exacto: 4/5. 80.000 de 100.000 dispara y 79.999 no.
    base = rasgo_demo(monto_recibido_crc=100_000)
    justo = base.model_copy(update={"monto_reenviado_crc": 80_000, "proporcion_pb": 8000})
    corto = base.model_copy(update={"monto_reenviado_crc": 79_999, "proporcion_pb": 7999})

    assert una("R-11", tx=_siguiente(), ctx=_ctx(justo), activa=True).reglas_disparadas
    assert not una("R-11", tx=_siguiente(), ctx=_ctx(corto), activa=True).reglas_disparadas


def prueba_r11_pide_los_saltos_minimos() -> None:
    corta = rasgo_demo(saltos=1)

    evaluacion = una("R-11", tx=_siguiente(), ctx=_ctx(corta), activa=True)

    assert evaluacion.reglas_disparadas == ()
    assert evaluacion.reglas_no_evaluadas == (), "es una regla evaluada que no disparo"


def prueba_r11_no_dispara_si_no_recibio_nada() -> None:
    vacio = rasgo_demo(monto_recibido_crc=0, monto_reenviado_crc=0, proporcion_pb=0, saltos=0)

    evaluacion = una("R-11", tx=_siguiente(), ctx=_ctx(vacio), activa=True)

    assert evaluacion.reglas_disparadas == ()
    assert evaluacion.reglas_no_evaluadas == ()


@pytest.mark.parametrize(
    ("rasgo", "fecha", "motivo"),
    [
        (None, CORTE, "no hay rasgos de grafo"),
        (rasgo_demo(cuenta="2233-4455"), CORTE, "es de 2233-4455"),
        (rasgo_demo(ventana_minutos=30), CORTE, "30 minutos"),
        (rasgo_demo(), DESDE - timedelta(minutes=1), "es anterior"),
        (rasgo_demo(), CORTE + timedelta(seconds=61), "el rasgo es viejo"),
    ],
)
def prueba_r11_sin_rasgo_valido_queda_no_evaluada_y_no_dispara(
    rasgo: RasgoRed | None, fecha: datetime, motivo: str
) -> None:
    evaluacion = una("R-11", tx=transaccion(fecha=fecha), ctx=_ctx(rasgo), activa=True)

    assert evaluacion.reglas_disparadas == ()
    [no_evaluada] = evaluacion.reglas_no_evaluadas
    assert motivo in no_evaluada.motivo


def prueba_r11_vigencia_inclusiva_en_el_limite() -> None:
    evaluacion = una(
        "R-11", tx=transaccion(fecha=CORTE + timedelta(seconds=60)), ctx=_ctx(rasgo_demo()),
        activa=True,
    )  # fmt: skip

    assert evaluacion.reglas_disparadas


def prueba_r11_sin_parametros_no_se_evalua() -> None:
    evaluacion = una("R-11", tx=_siguiente(), ctx=_ctx(rasgo_demo()), activa=True, parametros={})

    assert "faltan los parametros" in evaluacion.reglas_no_evaluadas[0].motivo
    assert regla("R-11").parametros["ventana_minutos"] == 15


# ---------------------------------------------------------------------------
# El pipeline y la forma del documento
# ---------------------------------------------------------------------------


def prueba_el_pipeline_termina_en_merge_idempotente() -> None:
    pipeline = construir_pipeline(DESDE, CORTE, 15, 60)

    assert [next(iter(etapa)) for etapa in pipeline] == [
        "$match",
        "$group",
        "$graphLookup",
        "$addFields",
        "$addFields",
        "$addFields",
        "$project",
        "$merge",
    ]
    assert pipeline[0]["$match"] == {"fecha": {"$gte": DESDE, "$lt": CORTE}}
    assert pipeline[-1]["$merge"] == {
        "into": "deteccion_estado",
        "on": "_id",
        "whenMatched": "replace",
        "whenNotMatched": "insert",
    }
    grafo = pipeline[2]["$graphLookup"]
    assert grafo["connectFromField"] == "cuenta_destino"
    assert grafo["connectToField"] == "sinpe_origen"
    assert grafo["maxDepth"] == SALTOS_MAXIMOS - 1


def prueba_el_documento_lleva_el_id_y_la_clase_del_validador() -> None:
    proyeccion = construir_pipeline(DESDE, CORTE, 15, 60)[-2]["$project"]

    assert proyeccion["clase"] == {"$literal": CLASE_RASGO}
    assert proyeccion["_id"] == {"$concat": [f"{NOMBRE_RASGOS}:", "$_id"]}
    assert id_rasgo(MULA) == "d3_rasgos_grafo:6033-9001"
    # El dinero se guarda como long: nunca un double.
    assert proyeccion["monto_recibido_crc"] == {"$toLong": "$recibido"}
    assert proyeccion["monto_reenviado_crc"] == {"$toLong": "$reenviado"}


def prueba_una_cuenta_sin_documento_se_lee_en_ceros_con_el_rango_de_la_corrida() -> None:
    corrida = {"ventana_minutos": 15, "desde": DESDE, "corte": CORTE, "vigencia_segundos": 60}

    rasgo = a_rasgo_red(None, corrida, "9999-9999")

    assert (rasgo.cuenta, rasgo.monto_recibido_crc, rasgo.saltos) == ("9999-9999", 0, 0)
    assert (rasgo.desde, rasgo.corte) == (DESDE, CORTE)


# ---------------------------------------------------------------------------
# Configuracion y D3
# ---------------------------------------------------------------------------


def prueba_la_vigencia_tiene_que_superar_el_intervalo_de_d3() -> None:
    uri = {"uri": "mongodb://x", "base": "b"}
    with pytest.raises(ValueError, match="vigencia_segundos"):
        Ajustes(
            mongo=uri,
            indicadores={"intervalo_segundos": 60},
            rasgos_grafo={"vigencia_segundos": 60},
        )
    Ajustes(mongo=uri, indicadores={"intervalo_segundos": 10}, rasgos_grafo={"vigencia_segundos": 60})
    assert AjustesRasgosGrafo().horizonte_horas == 24


def prueba_el_procedimiento_valida_sus_banderas() -> None:
    assert leer_argumentos([]).ventana_minutos is None
    assert leer_argumentos(["--ventana-minutos", "15", "--horas", "48"]).horas == 48
    with pytest.raises(SystemExit):
        leer_argumentos(["--ventana-minutos", "0"])
    with pytest.raises(SystemExit):
        leer_argumentos(["--horas", "0"])


class _Indicadores:
    async def recalcular(self, desde: date, hasta: date) -> int:
        return 1


class _Rasgos:
    def __init__(self, fallos: int = 0) -> None:
        self.cortes: list[datetime] = []
        self.fallos = fallos

    async def recalcular_si_activa(self, corte: datetime) -> int:
        self.cortes.append(corte)
        if len(self.cortes) <= self.fallos:
            raise RuntimeError("la base no responde")
        return 3

    async def recalcular(self, corte: datetime, *, ventana_minutos: int, horizonte: timedelta) -> int:
        return 0


@pytest.mark.asyncio
async def prueba_d3_recalcula_los_rasgos_en_la_misma_vuelta_y_late() -> None:
    supervisor = Supervisor(tolerancia_segundos=10)
    rasgos = _Rasgos()
    supervisor.lanzar(
        "d3_indicadores",
        d3(
            _Indicadores(), supervisor,
            ajustes=AjustesIndicadores(intervalo_segundos=0.01),
            hoy=lambda: date(2026, 11, 22),
            repo_rasgos=rasgos,
            ahora=lambda: CORTE,
        ),
    )  # fmt: skip
    await asyncio.sleep(0.1)

    fila = supervisor.informe().tarea("d3_indicadores")
    assert fila.eventos_procesados >= 2
    assert set(rasgos.cortes) == {CORTE}
    await supervisor.detener()


@pytest.mark.asyncio
async def prueba_un_fallo_de_los_rasgos_no_detiene_a_d3() -> None:
    supervisor = Supervisor(tolerancia_segundos=10)
    rasgos = _Rasgos(fallos=2)
    supervisor.lanzar(
        "d3_indicadores",
        d3(
            _Indicadores(), supervisor,
            ajustes=AjustesIndicadores(intervalo_segundos=0.01),
            hoy=lambda: date(2026, 11, 22),
            repo_rasgos=rasgos,
        ),
    )  # fmt: skip
    await asyncio.sleep(0.15)

    fila = supervisor.informe().tarea("d3_indicadores")
    assert len(rasgos.cortes) >= 3, "despues de los fallos vuelve a intentar"
    assert fila.eventos_procesados >= 1
    await supervisor.detener()


# ---------------------------------------------------------------------------
# demo-cadena-viva (D-68), la parte pura
# ---------------------------------------------------------------------------

CUENTAS_DEMO = {
    "8712-4455": "CTA-001", "6210-3387": "CTA-002", "6033-9001": "CTA-005",
    "7104-2288": "CTA-006", "8455-1177": "CTA-007",
}  # fmt: skip


def prueba_la_cadena_viva_termina_un_minuto_antes_de_ahora_con_ids_consecutivos() -> None:
    from app.deteccion.demo_cadena import armar_cadena

    ahora = datetime(2026, 11, 23, 20, 0, tzinfo=UTC)
    cadena = armar_cadena(ahora, 900_001, CUENTAS_DEMO | {"6001-3344": "x"})

    assert [t.transaccion_id for t in cadena.transacciones] == [
        "TXN-900001", "TXN-900002", "TXN-900003", "TXN-900004",
    ]  # fmt: skip
    assert cadena.disparo.transaccion_id == "TXN-900005"
    assert [t.monto_crc for t in cadena.transacciones] == [280_000, 276_000, 185_000, 90_000]
    assert cadena.transacciones[1].fecha - cadena.transacciones[0].fecha == timedelta(minutes=4)
    assert cadena.transacciones[-1].fecha == ahora - timedelta(minutes=1)
    assert all(t.fecha < ahora for t in cadena.transacciones)
    assert cadena.disparo.cuenta_destino == MULA and cadena.disparo.fecha == ahora


def prueba_la_cadena_viva_dice_que_cuentas_faltan() -> None:
    from app.deteccion.demo_cadena import armar_cadena

    with pytest.raises(LookupError, match="datos-demo"):
        armar_cadena(CORTE, 1, {"8712-4455": "CTA-001"})


def prueba_los_ids_de_la_cadena_viva_son_de_seis_digitos_y_siguen_al_mayor() -> None:
    """El rezago de D1 compara `_id` como texto: la cadena usa el asignador comun, el
    siguiente al mayor TXN de seis digitos, no un rango propio."""
    import re

    from app.deteccion.demo_cadena import armar_cadena
    from app.repos.ids_transaccion import numero_siguiente

    cadena = armar_cadena(CORTE, numero_siguiente("TXN-003000"), CUENTAS_DEMO | {"6001-3344": "x"})
    ids = [t.transaccion_id for t in (*cadena.transacciones, cadena.disparo)]

    assert ids == [f"TXN-00300{n}" for n in range(1, 6)]
    assert all(re.fullmatch(r"TXN-\d{6}", i) for i in ids), ids
    assert min(ids) > "TXN-003000"
