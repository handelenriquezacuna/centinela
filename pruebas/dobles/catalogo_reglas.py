"""Catalogo de reglas y politica para el doble de `reglas_deteccion`.

Copia de `scripts/semilla/03-catalogos.js` (H-07), con las descripciones reales. Es
copia a proposito, igual que la de `pruebas/unitarias/prueba_evaluador.py`: el caso
documentado (R-01 + R-03 + R-05 = 75, CRITICA) depende de estos numeros. Contra la
semilla de verdad lo verifica `pruebas/contrato/prueba_reglas_mongo.py`.
"""

from __future__ import annotations

from typing import Any

# (_id, codigo, descripcion, tipo, umbral, parametros, peso, activa)
_FILAS: list[tuple[str, str, str, str, float, dict[str, int | float], int, bool]] = [
    ("REG-001", "R-01", "Monto sobre 10x el promedio del cliente", "monto", 10, {}, 35, True),
    (
        "REG-002",
        "R-03",
        "Destino que el cliente nunca ha usado",
        "destino",
        0,
        {"dias_historial": 90},
        25,
        True,
    ),
    ("REG-003", "R-05", "Transaccion fuera del horario habitual", "horario", 0, {}, 15, True),
    (
        "REG-004",
        "R-07",
        "Destino en lista de riesgo vigente al momento de la transaccion",
        "lista_riesgo",
        1,
        {},
        40,
        True,
    ),
    (
        "REG-005",
        "R-09",
        "Velocidad anomala: rafaga de transferencias del mismo origen",
        "velocidad",
        3,
        {"ventana_minutos": 30, "monto_acumulado_crc": 300_000},
        30,
        True,
    ),
    (
        "REG-006",
        "R-11",
        "El destino reenvia casi todo lo recibido en pocos minutos",
        "red_mulas",
        0.8,
        {"ventana_minutos": 15, "saltos_minimos": 2},
        20,
        False,
    ),
]


def reglas_como_documentos() -> list[dict[str, Any]]:
    return [
        {
            "_id": regla_id,
            "codigo": codigo,
            "descripcion": descripcion,
            "tipo": tipo,
            "umbral": umbral,
            "parametros": dict(parametros),
            "peso": peso,
            "activa": activa,
            "version": 1,
            "esquema_version": 1,
        }
        for regla_id, codigo, descripcion, tipo, umbral, parametros, peso, activa in _FILAS
    ]


def politica_como_documento() -> dict[str, Any]:
    return {
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
        "notificacion": {"severidad_minima": "CRITICA", "ventana_agrupacion_minutos": 5},
        "esquema_version": 1,
    }


def cuerpo_simulacion_txn_001() -> dict[str, Any]:
    """Cuerpo de `POST /api/v1/reglas/simular` para el caso documentado.

    El contexto es el que armaria H-09 para TXN-001: destino nunca usado, la cadena de
    mulas reportada a las 23:10 (despues de la transaccion, asi que R-07 no cuenta) y
    una sola transferencia del origen en la ventana (no hay rafaga).
    """
    from pruebas.dobles.datos_demo import LISTAS_RIESGO, PERFILES, TRANSACCIONES

    transaccion = next(t for t in TRANSACCIONES if t.transaccion_id == "TXN-001")
    return {
        "transaccion": transaccion.model_dump(mode="json", by_alias=True),
        "contexto": {
            "perfil": PERFILES[0].model_dump(mode="json", by_alias=True),
            "dias_historial": 90,
            "destinos_conocidos": {"6210-3387": 12, "5544-9021": 8},
            "entradas_lista_riesgo": [
                entrada.model_dump(mode="json", by_alias=True)
                for entrada in LISTAS_RIESGO
                if entrada.cuenta == transaccion.cuenta_destino
            ],
            "ventana_origen": {
                "ventana_minutos": 30,
                "cantidad": 1,
                "monto_acumulado_crc": transaccion.monto_crc,
            },
        },
    }
