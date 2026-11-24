"""T-59 · Perfil de D1: cuanto cuesta cada paso por transaccion, antes de optimizar.

Script de MEDICION, no de produccion. No cambia el comportamiento del motor: corre el
`d1()` real con un repositorio que hereda de `RepoDeteccion` y un supervisor que hereda de
`Supervisor`, y los dos solo agregan relojes (`time.perf_counter`) alrededor de cada
metodo. Un `CommandListener` de pymongo cuenta cada comando que viaja al servidor, por
tipo y coleccion. `app/deteccion/` no tiene una sola linea de perfilado.

Tres modos:

    rafaga   10.000 transacciones de golpe con D1 corriendo: tiempo y comandos por paso.
    explain  explain de la ventana de R-09 con 1, 10 y 100 transacciones del mismo origen.
    lote     costo medido de leer lo mismo por lote (50, 200, 500) y estimacion de D-64.

Necesita una base sembrada (01, 02, 03) y un replica set. NUNCA la base `antifraude`:
pasa `--base` con un nombre propio. Ejemplo:

    CENTINELA_MONGO__URI='mongodb://localhost:27232/?replicaSet=rsfraude' \
        uv run python -m scripts.medicion.perfil_d1 rafaga --base antifraude_perfil_t59
"""

# B023: las funciones de `modo_lote` se definen y se invocan dentro de la misma vuelta del
# bucle; ninguna sobrevive a la iteracion, asi que capturar la variable del bucle es seguro.
# ruff: noqa: B023
from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pymongo import monitoring

from app.deteccion import d1 as modulo_d1
from app.deteccion.d1 import NOMBRE_D1, d1
from app.generador.ejecucion import ejecutar
from app.modelos import Transaccion
from app.nucleo.config import AjustesDeteccion, AjustesGenerador, obtener_ajustes
from app.nucleo.db import crear_cliente
from app.repos.deteccion import RepoDeteccion
from app.repos.generador import RepoGenerador
from app.supervision import Supervisor

TRANSACCIONES = 10_000
AJUSTES_D1 = AjustesDeteccion(
    espera_cursor_ms=100,
    reintentos=1,
    espera_inicial_segundos=0.05,
    espera_maxima_segundos=0.2,
    rezago_intervalo_segundos=2,
)
PASOS = (
    "cursor",
    "reglas",
    "politica",
    "origen",
    "contexto",
    "evaluar",
    "alerta",
    "token",
    "supervisor",
    "rezago",
)
IGNORADOS = {
    "hello",
    "isMaster",
    "ismaster",
    "ping",
    "endSessions",
    "saslStart",
    "saslContinue",
}

# Paso en curso: lo fijan los envoltorios y lo lee el escucha para atribuir cada comando.
PASO: contextvars.ContextVar[str] = contextvars.ContextVar("paso", default="otros")


# ---------------------------------------------------------------------------
# Escucha de comandos
# ---------------------------------------------------------------------------


def _coleccion(comando: dict[str, Any], nombre: str) -> str:
    valor = comando.get(nombre) if nombre != "getMore" else comando.get("collection")
    return str(valor) if valor is not None else "-"


def _etiqueta_contexto(nombre: str, coleccion: str) -> str:
    """Dentro de `armar_contexto` cada coleccion corresponde a un sub-paso."""
    return {
        ("find", "perfiles_comportamiento"): "ctx.perfil",
        ("distinct", "cuentas"): "ctx.destinos",
        ("aggregate", "transacciones"): "ctx.destinos+ventana",
        ("find", "listas_riesgo"): "ctx.listas",
    }.get((nombre, coleccion), "ctx.otro")


class Contador(monitoring.CommandListener):
    """Cuenta comandos por (paso, comando, coleccion) y suma su duracion de servidor."""

    def __init__(self) -> None:
        self._en_vuelo: dict[int, tuple[str, str, str]] = {}
        self.cuenta: dict[tuple[str, str, str], int] = defaultdict(int)
        self.micros: dict[tuple[str, str, str], int] = defaultdict(int)
        self.por_evento: list[tuple[str, str, str, int]] = []

    def started(self, evento: monitoring.CommandStartedEvent) -> None:
        if evento.command_name in IGNORADOS:
            return
        coleccion = _coleccion(evento.command, evento.command_name)
        paso = PASO.get()
        if paso == "contexto":
            if evento.command_name == "aggregate" and evento.command.get("pipeline"):
                etapa = evento.command["pipeline"][0].get("$match", {})
                paso_etiqueta = (
                    "ctx.ventana"
                    if isinstance(etapa.get("cuenta_origen"), str)
                    else "ctx.destinos"
                )
            else:
                paso_etiqueta = _etiqueta_contexto(evento.command_name, coleccion)
            paso = paso_etiqueta
        self._en_vuelo[evento.request_id] = (paso, evento.command_name, coleccion)

    def succeeded(self, evento: monitoring.CommandSucceededEvent) -> None:
        clave = self._en_vuelo.pop(evento.request_id, None)
        if clave is None:
            return
        self.cuenta[clave] += 1
        self.micros[clave] += evento.duration_micros
        EVENTO_ACTUAL.registrar_comando(clave, evento.duration_micros)

    def failed(self, evento: monitoring.CommandFailedEvent) -> None:
        self._en_vuelo.pop(evento.request_id, None)


# ---------------------------------------------------------------------------
# Registro por evento
# ---------------------------------------------------------------------------


@dataclass
class Registro:
    """Lo medido en UNA transaccion: ms por paso, comandos por paso y comando."""

    ms: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    comandos: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    ms_comando: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    inicio: float = 0.0
    total_ms: float = 0.0
    con_alerta: bool = False


class _Actual:
    """Puntero al registro vigente. El cursor del evento siguiente va a `previo`."""

    def __init__(self) -> None:
        self.registro = Registro()
        self.previo = Registro()
        self.recibiendo_cursor = False

    def registrar_comando(self, clave: tuple[str, str, str], micros: int) -> None:
        destino = self.previo if clave[0] == "cursor" else self.registro
        destino.comandos[clave[0]] += 1
        destino.ms_comando[clave[0]] += micros / 1000


EVENTO_ACTUAL = _Actual()
EVENTOS: list[Registro] = []


def _sumar(paso: str, desde: float, registro: Registro | None = None) -> None:
    (registro or EVENTO_ACTUAL.registro).ms[paso] += (
        time.perf_counter() - desde
    ) * 1000


# ---------------------------------------------------------------------------
# Repo y supervisor con relojes
# ---------------------------------------------------------------------------


class RepoMedido(RepoDeteccion):
    """Mismo comportamiento que `RepoDeteccion`; cada metodo mide cuanto tarda."""

    async def observar_transacciones(  # type: ignore[override]
        self, *, desde_token: Any, espera_ms: int
    ) -> Any:
        flujo = super().observar_transacciones(
            desde_token=desde_token, espera_ms=espera_ms
        )
        iterador = flujo.__aiter__()
        try:
            while True:
                _cerrar_evento()
                PASO.set("cursor")
                EVENTO_ACTUAL.previo = Registro()
                desde = time.perf_counter()
                try:
                    vuelta = await iterador.__anext__()
                except StopAsyncIteration:
                    return
                _sumar("cursor", desde, EVENTO_ACTUAL.previo)
                EVENTO_ACTUAL.registro = EVENTO_ACTUAL.previo
                EVENTO_ACTUAL.registro.inicio = time.perf_counter()
                PASO.set("otros")
                yield vuelta
        finally:
            await flujo.aclose()

    async def leer_reglas_activas(self) -> Any:
        PASO.set("reglas")
        desde = time.perf_counter()
        try:
            return await super().leer_reglas_activas()
        finally:
            _sumar("reglas", desde)
            PASO.set("otros")

    async def leer_politica_vigente(self) -> Any:
        PASO.set("politica")
        desde = time.perf_counter()
        try:
            return await super().leer_politica_vigente()
        finally:
            _sumar("politica", desde)
            PASO.set("otros")

    async def leer_origen(self, transaccion: Any) -> Any:
        PASO.set("origen")
        desde = time.perf_counter()
        try:
            return await super().leer_origen(transaccion)
        finally:
            _sumar("origen", desde)
            PASO.set("otros")

    async def armar_contexto(self, transaccion: Any, origen: Any, reglas: Any) -> Any:
        PASO.set("contexto")
        desde = time.perf_counter()
        try:
            return await super().armar_contexto(transaccion, origen, reglas)
        finally:
            _sumar("contexto", desde)
            PASO.set("otros")

    async def insertar_alerta(self, alerta: Any) -> bool:
        PASO.set("alerta")
        desde = time.perf_counter()
        try:
            return await super().insertar_alerta(alerta)
        finally:
            _sumar("alerta", desde)
            EVENTO_ACTUAL.registro.con_alerta = True
            PASO.set("otros")

    async def guardar_token(self, *args: Any, **kwargs: Any) -> None:
        PASO.set("token")
        desde = time.perf_counter()
        try:
            await super().guardar_token(*args, **kwargs)
        finally:
            _sumar("token", desde)
            PASO.set("otros")

    async def medir_rezago(self, disparador: str) -> Any:
        PASO.set("rezago")
        desde = time.perf_counter()
        try:
            return await super().medir_rezago(disparador)
        finally:
            _sumar("rezago", desde)
            PASO.set("otros")


class SupervisorMedido(Supervisor):
    """El latido y sus anexos: `latir`, `contar_evaluada`, `registrar_latencia`."""

    def latir(self, nombre: str) -> None:
        desde = time.perf_counter()
        super().latir(nombre)
        _sumar("supervisor", desde)

    def contar_evaluada(self, nombre: str) -> None:
        desde = time.perf_counter()
        super().contar_evaluada(nombre)
        _sumar("supervisor", desde)

    def registrar_latencia(self, nombre: str, milisegundos: float) -> None:
        desde = time.perf_counter()
        super().registrar_latencia(nombre, milisegundos)
        _sumar("supervisor", desde)


def _cerrar_evento() -> None:
    registro = EVENTO_ACTUAL.registro
    if registro.inicio:
        registro.total_ms = (time.perf_counter() - registro.inicio) * 1000
        EVENTOS.append(registro)
    EVENTO_ACTUAL.registro = Registro()


_evaluar_original = modulo_d1.evaluar


def _evaluar_medido(*args: Any, **kwargs: Any) -> Any:
    desde = time.perf_counter()
    try:
        return _evaluar_original(*args, **kwargs)
    finally:
        _sumar("evaluar", desde)


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------


def _p95(valores: list[float]) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(len(ordenados) * 0.95))]


def _fila(
    nombre: str, eventos: list[Registro], lista: list[float], comandos: int
) -> dict[str, Any]:
    llamadas = len(lista)
    return {
        "paso": nombre,
        "llamadas": llamadas,
        "ms_promedio_por_llamada": round(statistics.fmean(lista), 3) if lista else 0.0,
        "ms_p95_por_llamada": round(_p95(lista), 3),
        "ms_por_transaccion": round(sum(lista) / len(eventos), 3) if eventos else 0.0,
        "comandos_por_transaccion": round(comandos / len(eventos), 3)
        if eventos
        else 0.0,
    }


def _informe_rafaga(total_s: float, n: int) -> dict[str, Any]:
    eventos = [e for e in EVENTOS if e.ms.get("evaluar")]
    filas = []
    for paso in PASOS:
        lista = [e.ms[paso] for e in eventos if paso in e.ms]
        comandos = sum(e.comandos.get(paso, 0) for e in eventos)
        if paso == "cursor":
            comandos = sum(e.comandos.get("cursor", 0) for e in eventos)
        filas.append(_fila(paso, eventos, lista, comandos))
    # El contexto se abre por sub-paso con la duracion de servidor de cada comando.
    for sub in ("ctx.perfil", "ctx.destinos", "ctx.listas", "ctx.ventana"):
        lista = [e.ms_comando[sub] for e in eventos if sub in e.ms_comando]
        comandos = sum(e.comandos.get(sub, 0) for e in eventos)
        filas.append(_fila(sub + " (servidor)", eventos, lista, comandos))
    medidos = (
        "cursor",
        "reglas",
        "politica",
        "origen",
        "contexto",
        "evaluar",
        "alerta",
        "token",
        "supervisor",
        "rezago",
    )
    otros = [
        e.total_ms - sum(e.ms.get(p, 0.0) for p in medidos if p != "cursor")
        for e in eventos
    ]
    filas.append(_fila("otros (validacion, construir alerta, log)", eventos, otros, 0))
    totales = [e.total_ms + e.ms.get("cursor", 0.0) for e in eventos]
    filas.append(
        _fila(
            "TOTAL por transaccion (con cursor)",
            eventos,
            totales,
            sum(sum(e.comandos.values()) for e in eventos),
        )
    )
    con_alerta = [e for e in eventos if e.con_alerta]
    return {
        "transacciones_medidas": len(eventos),
        "segundos_totales": round(total_s, 1),
        "transacciones_por_segundo": round(n / total_s, 1),
        "alertas": len(con_alerta),
        "alerta_ms_cuando_escribe": round(
            statistics.fmean([e.ms["alerta"] for e in con_alerta]), 3
        )
        if con_alerta
        else 0,
        "filas": filas,
    }


def _comandos_por_tipo(contador: Contador, n: int) -> list[dict[str, Any]]:
    filas = []
    for (paso, nombre, coleccion), cuenta in sorted(contador.cuenta.items()):
        filas.append(
            {
                "paso": paso,
                "comando": nombre,
                "coleccion": coleccion,
                "total": cuenta,
                "por_transaccion": round(cuenta / n, 3),
                "ms_servidor_promedio": round(
                    contador.micros[(paso, nombre, coleccion)] / cuenta / 1000, 3
                ),
            }
        )
    return filas


# ---------------------------------------------------------------------------
# Modo rafaga
# ---------------------------------------------------------------------------


async def modo_rafaga(uri: str, base_nombre: str) -> dict[str, Any]:
    ajustes = obtener_ajustes()
    contador = Contador()
    cliente_d1 = crear_cliente(ajustes, escuchas=[contador])
    cliente_gen = crear_cliente(ajustes)
    base_d1 = cliente_d1[base_nombre]
    base_gen = cliente_gen[base_nombre]
    if await base_gen["reglas_deteccion"].count_documents({}) == 0:
        raise SystemExit(f"{base_nombre} no esta sembrada (falta reglas_deteccion)")
    for coleccion in (
        "transacciones",
        "alertas",
        "clientes",
        "cuentas",
        "perfiles_comportamiento",
        "listas_riesgo",
        "deteccion_estado",
    ):
        await base_gen[coleccion].delete_many({})

    modulo_d1.evaluar = _evaluar_medido  # type: ignore[assignment]
    supervisor = SupervisorMedido(tolerancia_segundos=30)
    repo = RepoMedido(base_d1)
    supervisor.lanzar(NOMBRE_D1, d1(repo, supervisor, ajustes=AJUSTES_D1))
    try:
        limite = time.monotonic() + 20
        while supervisor.informe().tarea(NOMBRE_D1).eventos_procesados < 1:
            if time.monotonic() > limite:
                raise SystemExit("D1 no abrio el change stream")
            await asyncio.sleep(0.05)
        EVENTOS.clear()
        contador.cuenta.clear()
        contador.micros.clear()

        inicio = time.monotonic()
        resumen = await ejecutar(
            RepoGenerador(base_gen),
            AjustesGenerador(clientes=500, transacciones=TRANSACCIONES),
            limpiar=False,
        )
        ultima = f"TXN-{TRANSACCIONES:06d}"
        while True:
            estado = await base_gen["deteccion_estado"].find_one({"_id": NOMBRE_D1})
            if estado is not None and estado.get("ultima_transaccion_id") == ultima:
                break
            if time.monotonic() - inicio > 600:
                raise SystemExit("D1 no termino en 600 s")
            await asyncio.sleep(0.2)
        total = time.monotonic() - inicio
    finally:
        await supervisor.detener()
        modulo_d1.evaluar = _evaluar_original  # type: ignore[assignment]
    _cerrar_evento()
    informe = _informe_rafaga(total, TRANSACCIONES)
    informe["insercion_s"] = round(resumen.segundos, 2)
    informe["comandos"] = _comandos_por_tipo(contador, informe["transacciones_medidas"])
    await cliente_d1.close()
    await cliente_gen.close()
    return informe


# ---------------------------------------------------------------------------
# Modo explain (R-09)
# ---------------------------------------------------------------------------


def _pipeline_ventana(
    origen: str, fecha: datetime, minutos: int, propia: str
) -> list[dict[str, Any]]:
    """El mismo pipeline de `RepoDeteccion._ventana_origen`, copiado para poder explicarlo."""
    return [
        {
            "$match": {
                "cuenta_origen": origen,
                "fecha": {"$gte": fecha - timedelta(minutes=minutos), "$lte": fecha},
                "_id": {"$ne": propia},
            }
        },
        {
            "$group": {
                "_id": None,
                "cantidad": {"$sum": 1},
                "monto": {"$sum": "$monto_crc"},
            }
        },
    ]


def _buscar(arbol: Any, clave: str) -> list[Any]:
    """Todos los valores de `clave` en un arbol de dicts/listas (el plan cambia de forma)."""
    hallados: list[Any] = []
    if isinstance(arbol, dict):
        for k, v in arbol.items():
            if k == clave:
                hallados.append(v)
            hallados.extend(_buscar(v, clave))
    elif isinstance(arbol, list):
        for v in arbol:
            hallados.extend(_buscar(v, clave))
    return hallados


def _etapas(arbol: Any) -> list[tuple[str, int]]:
    """(etapa, nReturned) de todo el arbol `executionStages`/`inputStage`."""
    salida: list[tuple[str, int]] = []
    if isinstance(arbol, dict):
        if "stage" in arbol and "nReturned" in arbol:
            salida.append((str(arbol["stage"]), int(arbol["nReturned"])))
        for v in arbol.values():
            salida.extend(_etapas(v))
    elif isinstance(arbol, list):
        for v in arbol:
            salida.extend(_etapas(v))
    return salida


async def modo_explain(base_nombre: str) -> dict[str, Any]:
    ajustes = obtener_ajustes()
    cliente = crear_cliente(ajustes)
    base = cliente[f"{base_nombre}_explain"]
    await cliente.drop_database(base.name)
    fin = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    ruido = [
        {
            "_id": f"TXN-{i:06d}",
            "cuenta_origen": f"CTA-{i % 500:03d}",
            "cuenta_destino": f"6000-{i % 997:04d}",
            "monto_crc": 10_000 + i,
            "fecha": fin - timedelta(minutes=i * 4),
        }
        for i in range(1, 10_001)
    ]
    resultados = []
    for k in (1, 10, 100):
        await base["transacciones"].drop()
        await base["transacciones"].create_index(
            [("cuenta_origen", 1), ("fecha", -1)],
            name="idx_transacciones_cuenta_origen_fecha",
        )
        await base["transacciones"].insert_many(ruido)
        misma = [
            {
                "_id": f"TXN-X{j:05d}",
                "cuenta_origen": "CTA-777",
                "cuenta_destino": f"6000-{j:04d}",
                "monto_crc": 50_000,
                "fecha": fin - timedelta(seconds=(k - j) * 10),
            }
            for j in range(k)
        ]
        await base["transacciones"].insert_many(misma)
        propia = f"TXN-X{k:05d}"
        pipeline = _pipeline_ventana("CTA-777", fin, 30, propia)
        explicacion = await base.command(
            {
                "explain": {
                    "aggregate": "transacciones",
                    "pipeline": pipeline,
                    "cursor": {},
                },
                "verbosity": "executionStats",
            }
        )
        stats = _buscar(explicacion, "executionStats")
        base_stats = stats[0] if stats else {}
        etapas = _etapas(explicacion)
        plan = [e for e in _buscar(explicacion, "stage")]
        # Bucket por minuto: un documento por (origen, minuto), K transacciones en pocos minutos.
        await base["ventana_minuto"].drop()
        await base["ventana_minuto"].create_index([("cuenta_origen", 1), ("minuto", 1)])
        cubos: dict[Any, dict[str, Any]] = defaultdict(
            lambda: {"cantidad": 0, "monto": 0}
        )
        for doc in misma:
            minuto = doc["fecha"].replace(second=0, microsecond=0)
            cubos[minuto]["cantidad"] += 1
            cubos[minuto]["monto"] += doc["monto_crc"]
        await base["ventana_minuto"].insert_many(
            [{"cuenta_origen": "CTA-777", "minuto": m, **c} for m, c in cubos.items()]
        )
        explicacion_cubo = await base.command(
            {
                "explain": {
                    "find": "ventana_minuto",
                    "filter": {
                        "cuenta_origen": "CTA-777",
                        "minuto": {"$gte": fin - timedelta(minutes=30), "$lte": fin},
                    },
                },
                "verbosity": "executionStats",
            }
        )
        stats_cubo = explicacion_cubo["executionStats"]
        resultados.append(
            {
                "transacciones_en_ventana": k,
                "plan": sorted({str(p) for p in plan}),
                "nReturned_raiz": base_stats.get("nReturned"),
                "nReturned_por_etapa": etapas,
                "totalKeysExamined": base_stats.get("totalKeysExamined"),
                "totalDocsExamined": base_stats.get("totalDocsExamined"),
                "executionTimeMillis": base_stats.get("executionTimeMillis"),
                "bucket_minuto": {
                    "documentos_de_bucket": len(cubos),
                    "nReturned": stats_cubo["nReturned"],
                    "totalKeysExamined": stats_cubo["totalKeysExamined"],
                    "totalDocsExamined": stats_cubo["totalDocsExamined"],
                },
            }
        )
    await cliente.drop_database(base.name)
    await cliente.close()
    return {"explain_r09": resultados}


# ---------------------------------------------------------------------------
# Modo lote
# ---------------------------------------------------------------------------


async def _tiempo(funcion: Any, repeticiones: int) -> list[float]:
    muestras = []
    for _ in range(repeticiones):
        desde = time.perf_counter()
        await funcion()
        muestras.append((time.perf_counter() - desde) * 1000)
    return muestras


async def modo_lote(base_nombre: str, perfil: dict[str, Any]) -> dict[str, Any]:
    """Mide la lectura por lote de lo que D1 hoy lee por transaccion.

    Lee de la base que dejo el modo rafaga (10.000 transacciones). Cada paso es la
    consulta por lote que propone D-64: una vez reglas y politica, y `$in` para perfiles,
    cuentas, clientes, destinos, listas y ventana. Destinos y ventana traen una
    proyeccion minima de lo que cae en el rango y cuentan en memoria (cada transaccion
    tiene su propia fecha de corte), que es lo que costaria de verdad.
    """
    ajustes = obtener_ajustes()
    contador = Contador()
    cliente = crear_cliente(ajustes, escuchas=[contador])
    base = cliente[base_nombre]
    repo = RepoDeteccion(base)
    scratch = cliente[f"{base_nombre}_lote"]
    salida: list[dict[str, Any]] = []
    por_tx_hoy = next(f for f in perfil["filas"] if f["paso"].startswith("TOTAL"))
    evaluar_ms = next(f for f in perfil["filas"] if f["paso"] == "evaluar")[
        "ms_por_transaccion"
    ]
    otros_ms = next(f for f in perfil["filas"] if f["paso"].startswith("otros"))[
        "ms_por_transaccion"
    ]
    cursor_ms = next(f for f in perfil["filas"] if f["paso"] == "cursor")[
        "ms_por_transaccion"
    ]
    PASO.set("lote")

    for n in (50, 200, 500):
        docs = (
            await base["transacciones"]
            .find({})
            .sort("_id", 1)
            .skip(5_000)
            .limit(n)
            .to_list(n)
        )
        txs = [Transaccion.model_validate(d) for d in docs]
        origenes = sorted({t.cuenta_origen for t in txs})
        destinos = sorted({t.cuenta_destino for t in txs})
        fechas = [t.fecha for t in txs]
        minima, maxima = min(fechas), max(fechas)
        cuentas_doc: list[dict[str, Any]] = []
        clientes_ids: list[str] = []
        todas_cuentas: list[str] = []

        async def reglas_y_politica() -> None:
            await repo.leer_reglas_activas()
            await repo.leer_politica_vigente()

        async def origen() -> None:
            nonlocal cuentas_doc, clientes_ids
            cuentas_doc = (
                await base["cuentas"].find({"_id": {"$in": origenes}}).to_list(None)
            )
            clientes_ids = sorted({c["cliente_id"] for c in cuentas_doc})
            await (
                base["clientes"]
                .find({"_id": {"$in": clientes_ids}}, {"nombre": 1})
                .to_list(None)
            )

        async def perfiles() -> None:
            await (
                base["perfiles_comportamiento"]
                .find({"_id": {"$in": clientes_ids}})
                .to_list(None)
            )

        async def destinos_conocidos() -> None:
            nonlocal todas_cuentas
            todas = (
                await base["cuentas"]
                .find({"cliente_id": {"$in": clientes_ids}}, {"_id": 1})
                .to_list(None)
            )
            todas_cuentas = [c["_id"] for c in todas]
            await (
                base["transacciones"]
                .find(
                    {
                        "cuenta_origen": {"$in": todas_cuentas},
                        "cuenta_destino": {"$in": destinos},
                        "fecha": {"$gte": minima - timedelta(days=30), "$lt": maxima},
                    },
                    {"cuenta_origen": 1, "cuenta_destino": 1, "fecha": 1},
                )
                .to_list(None)
            )

        async def listas() -> None:
            await (
                base["listas_riesgo"].find({"cuenta": {"$in": destinos}}).to_list(None)
            )

        async def ventana() -> None:
            await (
                base["transacciones"]
                .find(
                    {
                        "cuenta_origen": {"$in": origenes},
                        "fecha": {
                            "$gte": minima - timedelta(minutes=30),
                            "$lte": maxima,
                        },
                    },
                    {"cuenta_origen": 1, "fecha": 1, "monto_crc": 1},
                )
                .to_list(None)
            )

        k = max(1, round(n * 0.014))
        modelo = await base["alertas"].find({}).limit(k).to_list(k)
        if not modelo:
            modelo = [{"_id": "ALR-X", "transaccion_id": "TXN-X", "esquema_version": 2}]

        async def alertas() -> None:
            await scratch["alertas"].drop()
            await scratch["alertas"].create_index("transaccion_id", unique=True)
            lote = [
                {
                    **modelo[i % len(modelo)],
                    "_id": f"ALR-L{i}",
                    "transaccion_id": f"TXN-L{i}",
                }
                for i in range(k)
            ]
            desde = time.perf_counter()
            await scratch["alertas"].insert_many(lote, ordered=False)
            alertas.ultimo = (time.perf_counter() - desde) * 1000  # type: ignore[attr-defined]

        async def token() -> None:
            await scratch["deteccion_estado"].replace_one(
                {"_id": "t59"},
                {"_id": "t59", "token": {"_data": "00" * 40}},
                upsert=True,
            )

        rep = 7
        medidas: dict[str, float] = {}
        for nombre, funcion in (
            ("reglas+politica", reglas_y_politica),
            ("origen (cuentas+clientes)", origen),
            ("perfiles", perfiles),
            ("destinos (R-03)", destinos_conocidos),
            ("listas (R-07)", listas),
            ("ventana (R-09)", ventana),
            ("insert_many alertas", alertas),
            ("token", token),
        ):
            muestras = await _tiempo(funcion, rep)
            if nombre == "insert_many alertas":
                muestras = []
                for _ in range(rep):
                    await alertas()
                    muestras.append(alertas.ultimo)  # type: ignore[attr-defined]
            medidas[nombre] = round(statistics.median(muestras), 2)
        lectura = sum(
            v for c, v in medidas.items() if c not in ("insert_many alertas", "token")
        )
        escritura = medidas["insert_many alertas"] + medidas["token"]
        cpu_por_tx = evaluar_ms + otros_ms
        lote_ms = lectura + escritura + n * cpu_por_tx + n * cursor_ms
        por_tx_lote = lote_ms / n
        salida.append(
            {
                "lote": n,
                "alertas_esperadas_en_el_lote": k,
                "ms_lectura_por_lote": round(lectura, 1),
                "ms_escritura_por_lote": round(escritura, 1),
                "detalle_ms": medidas,
                "ms_cpu_por_tx": round(cpu_por_tx, 3),
                "ms_total_lote_estimado": round(lote_ms, 1),
                "ms_por_tx_lote": round(por_tx_lote, 3),
                "ms_por_tx_hoy": por_tx_hoy["ms_por_transaccion"],
                "tx_por_segundo_lote": round(1000 / por_tx_lote, 0),
                "ganancia_x": round(por_tx_hoy["ms_por_transaccion"] / por_tx_lote, 2),
                "espera_primera_alerta_ms": round(lote_ms, 0),
            }
        )
    await cliente.drop_database(scratch.name)
    await cliente.close()
    return {"lotes": salida}


# ---------------------------------------------------------------------------


async def principal() -> None:
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument("modo", choices=("rafaga", "explain", "lote", "todo"))
    analizador.add_argument(
        "--base", required=True, help="base de medicion, NUNCA antifraude"
    )
    analizador.add_argument(
        "--salida", default="", help="archivo JSON donde guardar el informe"
    )
    argumentos = analizador.parse_args()
    if argumentos.base == "antifraude":
        raise SystemExit("no se mide sobre la base antifraude (T-34)")
    uri = obtener_ajustes().mongo.uri
    informe: dict[str, Any] = {"uri": uri, "base": argumentos.base}
    if argumentos.modo in ("rafaga", "todo"):
        informe["rafaga"] = await modo_rafaga(uri, argumentos.base)
    if argumentos.modo in ("explain", "todo"):
        informe.update(await modo_explain(argumentos.base))
    if argumentos.modo in ("lote", "todo"):
        if "rafaga" not in informe:
            raise SystemExit(
                "el modo lote necesita el perfil: usa `todo` o pasa --salida de rafaga"
            )
        informe.update(await modo_lote(argumentos.base, informe["rafaga"]))
    texto = json.dumps(informe, indent=2, default=str, ensure_ascii=False)
    if argumentos.salida:
        await asyncio.to_thread(Path(argumentos.salida).write_text, texto, "utf-8")
    print(texto)


if __name__ == "__main__":
    asyncio.run(principal())
