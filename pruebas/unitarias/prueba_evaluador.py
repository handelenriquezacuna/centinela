"""H-08 · El evaluador de reglas, probado sin base de datos.

El catalogo y la politica de abajo copian `scripts/semilla/03-catalogos.js`. Es copia a
proposito: el caso documentado (R-01 + R-03 + R-05 = 75, CRITICA) depende de esos
numeros, y si alguien los cambia en la semilla esta prueba tiene que contarlo.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest

from app.deteccion import (
    ContextoEvaluacion,
    Evaluacion,
    PoliticaDeteccion,
    VentanaOrigen,
    evaluar,
)
from app.modelos import Severidad
from app.modelos.identidad import HorarioHabitual, PerfilComportamiento
from app.modelos.transaccional import EntradaListaRiesgo, Regla, Transaccion
from pruebas.dobles.datos_demo import LISTAS_RIESGO, PERFILES, TRANSACCIONES

TXN_001 = next(t for t in TRANSACCIONES if t.transaccion_id == "TXN-001")
PERFIL = PERFILES[0]

# (_id, codigo, tipo, umbral, parametros, peso, activa), tal cual la semilla.
_FILAS = [
    ("REG-001", "R-01", "monto", 10, {}, 35, True),
    ("REG-002", "R-03", "destino", 0, {"dias_historial": 90}, 25, True),
    ("REG-003", "R-05", "horario", 0, {}, 15, True),
    ("REG-004", "R-07", "lista_riesgo", 1, {}, 40, True),
    (
        "REG-005",
        "R-09",
        "velocidad",
        3,
        {"ventana_minutos": 30, "monto_acumulado_crc": 300_000},
        30,
        True,
    ),
    (
        "REG-006",
        "R-11",
        "red_mulas",
        0.8,
        {"ventana_minutos": 15, "saltos_minimos": 2},
        20,
        False,
    ),
]
CATALOGO = {
    codigo: {
        "_id": regla_id,
        "codigo": codigo,
        "descripcion": f"Regla {codigo}",
        "tipo": tipo,
        "umbral": umbral,
        "parametros": parametros,
        "peso": peso,
        "activa": activa,
        "version": 1,
    }
    for regla_id, codigo, tipo, umbral, parametros, peso, activa in _FILAS
}

POLITICA = PoliticaDeteccion.model_validate(
    {
        "_id": "POL-001",
        "descripcion": "Cortes de severidad y umbral de creacion de alerta",
        "vigente": True,
        "version": 1,
        "umbral_alerta": 40,
        "puntaje_maximo": 100,
        "cortes_severidad": [
            {"severidad": "MEDIA", "puntaje_minimo": 40, "puntaje_maximo": 54},
            {"severidad": "ALTA", "puntaje_minimo": 55, "puntaje_maximo": 69},
            {"severidad": "CRITICA", "puntaje_minimo": 70, "puntaje_maximo": 100},
        ],
        "notificacion": {
            "severidad_minima": "CRITICA",
            "ventana_agrupacion_minutos": 5,
        },
        "esquema_version": 1,
    }
)


def regla(clave: str, **cambios: object) -> Regla:
    return Regla.model_validate({**CATALOGO[clave], **cambios})


def reglas_activas() -> list[Regla]:
    return [regla(codigo) for codigo in CATALOGO]


def contexto(**cambios: object) -> ContextoEvaluacion:
    """Contexto de TXN-001 tal como lo armaria H-09: destino nunca usado, la cadena de
    mulas reportada a las 23:10 (despues de la transaccion), una sola transferencia."""
    base: dict[str, object] = {
        "perfil": PERFIL,
        "dias_historial": 90,
        "destinos_conocidos": {"6210-3387": 12, "5544-9021": 8},
        "entradas_lista_riesgo": tuple(
            e for e in LISTAS_RIESGO if e.cuenta == TXN_001.cuenta_destino
        ),
        "ventana_origen": VentanaOrigen(
            ventana_minutos=30, cantidad=1, monto_acumulado_crc=TXN_001.monto_crc
        ),
    }
    return ContextoEvaluacion.model_validate({**base, **cambios})


def transaccion(**cambios: object) -> Transaccion:
    return TXN_001.model_copy(update=cambios)


def una(
    clave: str,
    tx: Transaccion | None = None,
    ctx: ContextoEvaluacion | None = None,
    **cambios: object,
) -> Evaluacion:
    return evaluar(
        tx or TXN_001, ctx or contexto(), [regla(clave, **cambios)], POLITICA
    )


def codigos(evaluacion: Evaluacion) -> list[str]:
    return [d.codigo for d in evaluacion.reglas_disparadas]


# ---------------------------------------------------------------------------
# El caso documentado
# ---------------------------------------------------------------------------


def prueba_el_caso_de_uso_da_75_critica() -> None:
    evaluacion = evaluar(TXN_001, contexto(), reglas_activas(), POLITICA)

    assert codigos(evaluacion) == ["R-01", "R-03", "R-05"]
    assert [d.peso for d in evaluacion.reglas_disparadas] == [35, 25, 15]
    assert evaluacion.puntaje == 75
    assert evaluacion.severidad is Severidad.CRITICA
    assert evaluacion.genera_alerta
    assert evaluacion.regla_ids == ["REG-001", "REG-002", "REG-003"]
    assert all(d.version == 1 for d in evaluacion.reglas_disparadas)
    assert (evaluacion.politica_id, evaluacion.politica_version) == ("POL-001", 1)
    # R-11 esta apagada: ni se evalua ni se reporta.
    assert evaluacion.reglas_no_evaluadas == ()


def prueba_las_explicaciones_muestran_observado_contra_umbral() -> None:
    explicaciones = {
        d.codigo: d.explicacion
        for d in evaluar(
            TXN_001, contexto(), reglas_activas(), POLITICA
        ).reglas_disparadas
    }

    assert "280.000 CRC" in explicaciones["R-01"]
    assert "11,2 veces" in explicaciones["R-01"]
    assert "25.000 CRC" in explicaciones["R-01"]
    assert "0 transferencias previas en 90 dias" in explicaciones["R-03"]
    assert "22:47 hora de Costa Rica" in explicaciones["R-05"]
    assert "07:00-19:00" in explicaciones["R-05"]


# ---------------------------------------------------------------------------
# Una por regla: dispara y no dispara
# ---------------------------------------------------------------------------


def prueba_r01_dispara_sobre_el_multiplo_y_no_en_el_borde() -> None:
    borde = PERFIL.promedio_crc * 10

    assert codigos(una("R-01", transaccion(monto_crc=borde + 1))) == ["R-01"]
    assert codigos(una("R-01", transaccion(monto_crc=borde))) == []


def prueba_r01_sube_el_umbral_y_txn_001_deja_de_disparar() -> None:
    assert codigos(una("R-01", umbral=20)) == []


def prueba_r03_destino_nuevo_dispara_y_conocido_no() -> None:
    conocido = contexto(destinos_conocidos={TXN_001.cuenta_destino: 1})

    assert codigos(una("R-03")) == ["R-03"]
    assert codigos(una("R-03", ctx=conocido)) == []
    assert codigos(una("R-03", ctx=conocido, umbral=1)) == ["R-03"]


def prueba_r05_fuera_de_horario_dispara_y_dentro_no() -> None:
    a_las_diez = transaccion(fecha=TXN_001.fecha - timedelta(hours=12, minutes=47))

    assert codigos(una("R-05")) == ["R-05"]
    assert codigos(una("R-05", a_las_diez)) == []


def prueba_r05_la_holgura_perdona_el_desfase() -> None:
    # 22:47 queda 227 minutos despues de las 19:00.
    assert codigos(una("R-05", umbral=227)) == []
    assert codigos(una("R-05", umbral=226)) == ["R-05"]


def prueba_r05_horario_que_cruza_la_medianoche() -> None:
    nocturno = PerfilComportamiento.model_validate(
        {
            **PERFIL.model_dump(by_alias=True),
            "horario_habitual": HorarioHabitual(inicio="22:00", fin="06:00"),
        }
    )

    assert codigos(una("R-05", ctx=contexto(perfil=nocturno))) == []


def prueba_r07_cuenta_reportada_antes_dispara() -> None:
    reportada = EntradaListaRiesgo(
        _id="RSK-009",
        cuenta=TXN_001.cuenta_destino,
        motivo="Mula conocida",
        fecha_reporte=TXN_001.fecha,
    )

    evaluacion = una("R-07", ctx=contexto(entradas_lista_riesgo=(reportada,)))

    assert codigos(evaluacion) == ["R-07"]
    assert evaluacion.puntaje == 40
    assert evaluacion.severidad is Severidad.MEDIA


def prueba_r07_reportada_despues_o_no_vigente_no_dispara() -> None:
    no_vigente = EntradaListaRiesgo(
        _id="RSK-009",
        cuenta=TXN_001.cuenta_destino,
        motivo="Revocada",
        fecha_reporte=TXN_001.fecha - timedelta(days=1),
        vigente=False,
    )

    # La entrada de datos_demo es de las 23:10, despues de las 22:47.
    assert codigos(una("R-07")) == []
    assert codigos(una("R-07", ctx=contexto(entradas_lista_riesgo=(no_vigente,)))) == []


def prueba_r09_rafaga_dispara_y_transferencia_sola_no() -> None:
    rafaga = contexto(
        ventana_origen=VentanaOrigen(
            ventana_minutos=30, cantidad=3, monto_acumulado_crc=300_000
        )
    )
    rafaga_chica = contexto(
        ventana_origen=VentanaOrigen(
            ventana_minutos=30, cantidad=3, monto_acumulado_crc=299_999
        )
    )

    assert codigos(una("R-09", ctx=rafaga)) == ["R-09"]
    assert codigos(una("R-09", ctx=rafaga_chica)) == []
    assert codigos(una("R-09")) == []


def prueba_r09_contexto_con_otra_ventana_no_se_evalua() -> None:
    viejo = contexto(
        ventana_origen=VentanaOrigen(
            ventana_minutos=15, cantidad=5, monto_acumulado_crc=900_000
        )
    )

    evaluacion = una("R-09", ctx=viejo)

    assert codigos(evaluacion) == []
    assert "15 minutos" in evaluacion.reglas_no_evaluadas[0].motivo


def prueba_r11_activa_queda_no_evaluada_con_motivo() -> None:
    evaluacion = una("R-11", activa=True)

    assert codigos(evaluacion) == []
    [no_evaluada] = evaluacion.reglas_no_evaluadas
    assert (no_evaluada.codigo, no_evaluada.version) == ("R-11", 1)
    assert "H-23" in no_evaluada.motivo


# ---------------------------------------------------------------------------
# Robustez
# ---------------------------------------------------------------------------


def prueba_regla_inactiva_no_se_evalua() -> None:
    evaluacion = una("R-01", activa=False)

    assert evaluacion.reglas_disparadas == ()
    assert evaluacion.reglas_no_evaluadas == ()
    assert evaluacion.puntaje == 0


def prueba_tipo_desconocido_no_revienta() -> None:
    reglas = [
        *reglas_activas(),
        regla("R-01", _id="REG-099", codigo="R-99", tipo="biometria"),
    ]

    evaluacion = evaluar(TXN_001, contexto(), reglas, POLITICA)

    assert evaluacion.puntaje == 75
    [no_evaluada] = evaluacion.reglas_no_evaluadas
    assert no_evaluada.regla_id == "REG-099"
    assert "biometria" in no_evaluada.motivo


def prueba_sin_perfil_r01_y_r05_no_se_evaluan() -> None:
    evaluacion = evaluar(TXN_001, contexto(perfil=None), reglas_activas(), POLITICA)

    assert codigos(evaluacion) == ["R-03"]
    assert [n.codigo for n in evaluacion.reglas_no_evaluadas] == ["R-01", "R-05"]


def prueba_regla_sin_version_no_se_evalua() -> None:
    evaluacion = una("R-01", version=None)

    assert evaluacion.reglas_disparadas == ()
    assert "version" in evaluacion.reglas_no_evaluadas[0].motivo


def prueba_el_puntaje_se_topa_en_100() -> None:
    rafaga = contexto(
        entradas_lista_riesgo=(
            EntradaListaRiesgo(
                _id="RSK-009",
                cuenta=TXN_001.cuenta_destino,
                motivo="Mula",
                fecha_reporte=TXN_001.fecha,
            ),
        ),
        ventana_origen=VentanaOrigen(
            ventana_minutos=30, cantidad=4, monto_acumulado_crc=900_000
        ),
    )

    evaluacion = evaluar(TXN_001, rafaga, reglas_activas(), POLITICA)

    assert sum(d.peso for d in evaluacion.reglas_disparadas) == 145
    assert evaluacion.puntaje == 100
    assert evaluacion.severidad is Severidad.CRITICA


@pytest.mark.parametrize(
    ("puntaje", "severidad"),
    [
        (39, None),
        (40, Severidad.MEDIA),
        (54, Severidad.MEDIA),
        (55, Severidad.ALTA),
        (69, Severidad.ALTA),
        (70, Severidad.CRITICA),
        (100, Severidad.CRITICA),
    ],
)
def prueba_cortes_de_severidad_en_su_borde(
    puntaje: int, severidad: Severidad | None
) -> None:
    evaluacion = una("R-01", peso=puntaje)

    assert evaluacion.puntaje == puntaje
    assert evaluacion.severidad == severidad
    assert evaluacion.genera_alerta is (severidad is not None)


def prueba_los_cortes_salen_de_la_politica_y_no_del_codigo() -> None:
    otra = POLITICA.model_copy(
        update={
            "cortes_severidad": tuple(
                c.model_copy(update={"severidad": Severidad.ALTA})
                for c in POLITICA.cortes_severidad
            )
        }
    )

    assert (
        evaluar(TXN_001, contexto(), reglas_activas(), otra).severidad is Severidad.ALTA
    )


def prueba_el_evaluador_es_puro() -> None:
    """Sin driver, sin repos, sin API, sin reloj."""
    fuente = Path(__file__).resolve().parents[2] / "app" / "deteccion" / "evaluador.py"
    arbol = ast.parse(fuente.read_text(encoding="utf-8"))
    modulos = {
        n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) and n.module
    } | {a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names}
    nombres = {n.attr for n in ast.walk(arbol) if isinstance(n, ast.Attribute)}

    assert not {
        m for m in modulos if m.startswith(("app.api", "app.repos", "pymongo", "bson"))
    }
    assert not {"now", "utcnow", "today", "time"} & nombres


def prueba_el_monto_del_caso_no_supera_el_tope_de_sinpe_movil() -> None:
    """D-18: el caso documentado debe ser posible en SINPE Movil (tope maximo 300.000)."""
    from pruebas.dobles.datos_demo import PERFILES

    assert TXN_001.monto_crc <= 300_000
    assert all(p.maximo_crc <= 300_000 for p in PERFILES)
    assert TXN_001.monto_crc > 10 * PERFILES[0].promedio_crc
