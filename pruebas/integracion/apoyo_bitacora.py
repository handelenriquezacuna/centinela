"""Datos de la bitacora de auditoria (T-84) en una base desechable.

Un volumen de relleno (miles de alertas con su historial, notificaciones, listas y casos, para
medir el pipeline) y un puñado de objetos con agentes y fechas conocidas (AGT-901/AGT-902, a
partir de `T0`) para afirmar con exactitud. Las ediciones de reglas y politica y el alta de la
lista de riesgo pasan por los repositorios reales, no por documentos escritos a mano: lo que la
bitacora lee es lo que el sistema de verdad deja.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.repos.carga_demo import a_entero_64
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.listas_riesgo import RepoListasRiesgo
from app.repos.reglas import RepoReglas
from pruebas.conftest import BASE_SEMILLA

BASE_BITACORA = "antifraude_pruebas_bitacora"
COLECCIONES = (
    "alertas",
    "reglas_deteccion",
    "politica_deteccion",
    "listas_riesgo",
    "casos",
    "notificaciones",
)
# Despues de todo el relleno (que termina en noviembre): lo conocido queda siempre arriba.
T0 = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
INICIO_RELLENO = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
AGENTES_RELLENO = ("AGT-001", "AGT-002", "AGT-003", "AGT-004", "AGT-005")
FINALES = ("confirmada", "descartada", "escalada")


def alerta(numero: int, historial: list[dict[str, Any]], *, estado: str) -> dict[str, Any]:
    return {
        "_id": f"ALR-{numero:06d}",
        "transaccion_id": f"TXN-{numero:06d}",
        "cuenta_origen": "8712-4455",
        "cuenta_destino": f"6033-{numero % 9000 + 1000:04d}",
        "monto_crc": 100_000 + numero,
        "moneda": "CRC",
        "cliente_nombre": "Cliente de prueba",
        "reglas_disparadas": ["REG-001"],
        "puntaje": 40 + numero % 50,
        "severidad": ("MEDIA", "ALTA", "CRITICA")[numero % 3],
        "estado": estado,
        "fecha_creacion": historial[0]["fecha"],
        "historial": historial,
        "esquema_version": 1,
    }


def _relleno(n_alertas: int) -> dict[str, list[dict[str, Any]]]:
    alertas, notificaciones, casos, listas = [], [], [], []
    for n in range(1, n_alertas + 1):
        creada = INICIO_RELLENO + timedelta(minutes=37 * n)
        agente = AGENTES_RELLENO[n % len(AGENTES_RELLENO)]
        historial: list[dict[str, Any]] = [{"accion": "nueva", "agente_id": None, "fecha": creada}]
        estado = "nueva"
        if n % 4:
            estado = "en_revision"
            historial.append(
                {"accion": "en_revision", "agente_id": agente, "fecha": creada + timedelta(minutes=5)}
            )
        if n % 4 > 1:
            estado = FINALES[n % 3]
            historial.append(
                {
                    "accion": estado,
                    "agente_id": agente,
                    "fecha": creada + timedelta(minutes=15),
                    "nota": f"nota {n}",
                }
            )
        alertas.append(alerta(n, historial, estado=estado))
        if n % 2 == 0:
            notificaciones.append(
                {
                    "_id": f"NOT-{n:06d}",
                    "alerta_id": f"ALR-{n:06d}",
                    "canal": "correo",
                    "estado_envio": "enviado",
                    "fecha": creada + timedelta(seconds=30),
                    "esquema_version": 1,
                }
            )
        if n % 15 == 0:
            casos.append(
                {
                    "_id": f"CAS-{n:06d}",
                    "alertas": [f"ALR-{n:06d}"],
                    "estado": "investigacion",
                    "fecha_apertura": creada + timedelta(minutes=15),
                    "esquema_version": 1,
                }
            )
        if n % 10 == 0:
            listas.append(
                {
                    "_id": f"RSK-{n:04d}",
                    "cuenta": f"7000-{n % 9000 + 1000:04d}",
                    "motivo": "relleno",
                    "fecha_reporte": creada + timedelta(hours=1),
                    "vigente": True,
                    "esquema_version": 1,
                }
            )
    return {
        "alertas": alertas,
        "notificaciones": notificaciones,
        "casos": casos,
        "listas_riesgo": listas,
    }


def _a_mano() -> dict[str, list[dict[str, Any]]]:
    tomada1 = T0 + timedelta(hours=1)
    escalada = T0 + timedelta(hours=2)
    return {
        "alertas": [
            alerta(
                900001,
                [
                    {"accion": "nueva", "agente_id": None, "fecha": T0},
                    {"accion": "en_revision", "agente_id": "AGT-901", "fecha": tomada1, "nota": "la tomo"},
                    {"accion": "escalada", "agente_id": "AGT-901", "fecha": escalada, "nota": "a fraude"},
                ],
                estado="escalada",
            ),
            alerta(
                900002,
                [
                    {"accion": "nueva", "agente_id": None, "fecha": T0},
                    {"accion": "en_revision", "agente_id": "AGT-902", "fecha": tomada1},
                    {"accion": "descartada", "agente_id": "AGT-902", "fecha": escalada, "nota": "falso positivo"},
                ],
                estado="descartada",
            ),
        ],
        "casos": [
            {
                "_id": "CAS-900001",
                "alertas": ["ALR-900001"],
                "estado": "investigacion",
                "fecha_apertura": escalada,
                "cuenta_destino": "6033-9001",
                "agente_id": "AGT-901",
                "esquema_version": 1,
            },
            # De antes de T-56: sin fecha de apertura, no entra en la bitacora.
            {"_id": "CAS-900002", "alertas": ["ALR-900002"], "estado": "investigacion", "esquema_version": 1},
        ],
        "notificaciones": [
            {
                "_id": "NOT-900001",
                "alerta_id": "ALR-900001",
                "canal": "correo",
                "estado_envio": "enviado",
                "fecha": escalada + timedelta(minutes=1),
                "intentos": 1,
                "esquema_version": 1,
            },
            {
                "_id": "NOT-900002",
                "alerta_id": "ALR-900002",
                "canal": "sms",
                "estado_envio": "fallido",
                "fecha": escalada + timedelta(minutes=2),
                "detalle_error": "sin cobertura",
                "esquema_version": 1,
            },
        ],
    }


async def preparar_base(cliente: Any, n_alertas: int) -> Any:
    """Crea `BASE_BITACORA` con validadores reales, el catalogo, el relleno y lo conocido."""
    trabajo = cliente[BASE_SEMILLA]
    validadores = await obtener_validadores(trabajo)
    faltan = [c for c in COLECCIONES if c not in validadores]
    if faltan:
        pytest.fail(f"sin validador en {BASE_SEMILLA} para {faltan}")
    await cliente.drop_database(BASE_BITACORA)
    base = cliente[BASE_BITACORA]
    for nombre in COLECCIONES:
        await recrear_con_validador(base, nombre, validadores[nombre])
    # Los indices de 02-indices.js que tocan estas consultas.
    await base["alertas"].create_index("transaccion_id", unique=True, name="idx_alertas_transaccion_id")
    await base["notificaciones"].create_index(
        [("alerta_id", 1), ("canal", 1)], unique=True, name="idx_notificaciones_alerta_canal"
    )
    await base["notificaciones"].create_index([("fecha", -1)], name="idx_notificaciones_fecha")
    await base["listas_riesgo"].create_index("cuenta", unique=True, name="idx_listas_riesgo_cuenta")
    for indice in INDICES_BITACORA:
        await base["alertas"].create_index(**indice)
    for nombre in ("reglas_deteccion", "politica_deteccion"):
        documentos = await trabajo[nombre].find({}).to_list(length=None)
        assert documentos, f"{BASE_SEMILLA}.{nombre} vacia; correr 03-catalogos.js"
        await base[nombre].insert_many(documentos)

    contenido = _relleno(n_alertas)
    mano = _a_mano()
    for coleccion in ("alertas", "notificaciones", "casos", "listas_riesgo"):
        documentos = contenido.get(coleccion, []) + mano.get(coleccion, [])
        if documentos:
            await base[coleccion].insert_many([a_entero_64(d) for d in documentos])

    reglas = RepoReglas(base)
    regla = await reglas.obtener("REG-001")
    assert regla is not None
    editada = await reglas.editar(regla, {"umbral": 150000.0, "peso": 30}, T0 + timedelta(hours=3))
    assert editada is not None
    editada = await reglas.editar(editada, {"activa": False}, T0 + timedelta(hours=4))
    assert editada is not None
    politica = await reglas.politica_documento()
    assert politica is not None
    assert await reglas.editar_politica(politica, {"umbral_alerta": 40}, T0 + timedelta(hours=5))
    await RepoListasRiesgo(base).marcar(
        "6033-9901", motivo="cuenta mula reportada", agente_id="AGT-901", ahora=T0 + timedelta(hours=6)
    )
    return base


# Indices que agrega T-84 a `alertas` (02-indices.js); la medicion de docs/06 los justifica.
INDICES_BITACORA: tuple[dict[str, Any], ...] = (
    {"keys": [("historial.fecha", -1)], "name": "idx_alertas_historial_fecha"},
    {
        "keys": [("historial.agente_id", 1), ("historial.fecha", -1)],
        "name": "idx_alertas_historial_agente_fecha",
    },
)
