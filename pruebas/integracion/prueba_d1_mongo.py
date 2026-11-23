"""H-09 · D1 contra MongoDB de verdad: el caso documentado y la resiliencia.

Necesita el replica set (un change stream no existe sin oplog); se salta solo si no
responde. Corre en una base propia, `antifraude_pruebas_d1`, que se tira al terminar:
ni la base de trabajo ni la de las otras pruebas quedan con alertas de D1.

Lo que se usa de verdad, sin dobles:

- los validadores `$jsonSchema` de `transacciones`, `alertas` y `deteccion_estado`,
  copiados de la base de trabajo `antifraude` (los siembra `01-colecciones.js`): si
  la alerta o el token que escribe D1 no cumplen el validador, la prueba falla;
- el indice unico `idx_alertas_transaccion_id`, el mismo de `02-indices.js`: es lo
  que impide el duplicado, no el codigo de D1;
- el catalogo real de reglas y la politica de H-07, copiados de `antifraude`
  (los siembra `03-catalogos.js`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio

from app.deteccion.d1 import NOMBRE_D1, d1
from app.modelos import ZONA_COSTA_RICA, Transaccion
from app.nucleo.config import AjustesDeteccion
from app.repos.carga_demo import a_entero_64, cargar_demo
from app.repos.deteccion import RepoDeteccion
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.supervision import Supervisor
from pruebas.conftest import BASE_SEMILLA
from pruebas.dobles.datos_demo import DATOS_DEMO, TRANSACCIONES

pytestmark = pytest.mark.mongo

BASE_DE_TRABAJO = BASE_SEMILLA  # semilla propia de las pruebas, no `antifraude`
BASE_D1 = "antifraude_pruebas_d1"
CON_VALIDADOR = ("transacciones", "alertas", "deteccion_estado")
DE_IDENTIDAD = ("clientes", "cuentas", "perfiles_comportamiento", "listas_riesgo")
CATALOGO = ("reglas_deteccion", "politica_deteccion")

AJUSTES = AjustesDeteccion(
    espera_cursor_ms=100,
    reintentos=1,
    espera_inicial_segundos=0.05,
    espera_maxima_segundos=0.2,
)
LIMITE_SEGUNDOS = 20


@pytest_asyncio.fixture
async def base_d1(base_mongo: Any) -> AsyncIterator[Any]:
    """Base desechable con validadores, indice unico, identidad del demo y catalogo."""
    cliente = base_mongo.client
    trabajo = cliente[BASE_DE_TRABAJO]
    validadores = await obtener_validadores(trabajo)
    faltan = [nombre for nombre in CON_VALIDADOR if nombre not in validadores]
    if faltan:
        pytest.skip(
            f"sin validador en {BASE_DE_TRABAJO} para {faltan}; correr "
            "scripts/semilla/01-colecciones.js"
        )

    await cliente.drop_database(BASE_D1)
    base = cliente[BASE_D1]
    for nombre in CON_VALIDADOR:
        await recrear_con_validador(base, nombre, validadores[nombre])
    await base["alertas"].create_index(
        "transaccion_id", unique=True, name="idx_alertas_transaccion_id"
    )
    await cargar_demo(base, {nombre: DATOS_DEMO[nombre] for nombre in DE_IDENTIDAD})
    for nombre in CATALOGO:
        documentos = await trabajo[nombre].find({}).to_list(length=None)
        if not documentos:
            pytest.skip(f"{BASE_DE_TRABAJO}.{nombre} vacia; correr 03-catalogos.js")
        await base[nombre].insert_many(documentos)

    try:
        yield base
    finally:
        await cliente.drop_database(BASE_D1)


async def _esperar(condicion: Callable[[], Awaitable[bool]], que: str) -> None:
    limite = asyncio.get_running_loop().time() + LIMITE_SEGUNDOS
    while not await condicion():
        if asyncio.get_running_loop().time() > limite:
            raise AssertionError(f"pasaron {LIMITE_SEGUNDOS} s esperando {que}")
        await asyncio.sleep(0.05)


@asynccontextmanager
async def _d1_corriendo(base: Any) -> AsyncIterator[Supervisor]:
    """D1 lanzado como en el lifespan. Cede el control cuando el flujo ya esta abierto
    (primer latido), para que ninguna insercion de la prueba ocurra antes del watch."""
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(RepoDeteccion(base), supervisor, ajustes=AJUSTES))

    async def abierto() -> bool:
        return supervisor.informe().tarea(NOMBRE_D1).eventos_procesados >= 1

    try:
        await _esperar(abierto, "el primer latido de D1")
        yield supervisor
    finally:
        await supervisor.detener()


# El caso y su contraste (TXN-001..004). La cadena de mulas del demo (TXN-005..007) queda
# fuera: esta prueba mide el caso de 75 puntos, y la cadena tiene sus propias reglas.
CASO_Y_CONTRASTE = [t for t in TRANSACCIONES if t.transaccion_id <= "TXN-004"]


async def _procesada_hasta(base: Any, transaccion_id: str) -> None:
    async def llego() -> bool:
        estado = await base["deteccion_estado"].find_one({"_id": NOMBRE_D1})
        return estado is not None and estado.get("ultima_transaccion_id") == transaccion_id

    await _esperar(llego, f"que D1 guarde el token de {transaccion_id}")


async def _insertar(base: Any, transacciones: list[Transaccion]) -> None:
    await base["transacciones"].insert_many(
        [a_entero_64(transaccion.a_documento()) for transaccion in transacciones]
    )


def _noche(dia: int, transaccion_id: str, destino: str) -> Transaccion:
    """Transferencia nocturna grande de Maria a un destino nuevo: R-01 + R-03 + R-05."""
    return Transaccion(
        _id=transaccion_id,
        cuenta_origen="CTA-001",
        cuenta_destino=destino,
        monto_crc=600_000,
        fecha=datetime(2026, 9, dia, 23, 15, tzinfo=ZONA_COSTA_RICA),
    )


def _oficina(dia: int, transaccion_id: str) -> Transaccion:
    """Transferencia chica en horario y a un destino habitual: no alcanza el umbral."""
    return Transaccion(
        _id=transaccion_id,
        cuenta_origen="CTA-001",
        cuenta_destino="6210-3387",
        monto_crc=20_000,
        fecha=datetime(2026, 9, dia, 10, 0, tzinfo=ZONA_COSTA_RICA),
    )


async def _alertas_por_transaccion(base: Any) -> dict[str, int]:
    grupos = await (
        await base["alertas"].aggregate(
            [{"$group": {"_id": "$transaccion_id", "cantidad": {"$sum": 1}}}]
        )
    ).to_list(length=None)
    return {grupo["_id"]: grupo["cantidad"] for grupo in grupos}


# ---------------------------------------------------------------------------
# El caso documentado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_transaccion_del_caso_produce_la_alerta_de_75(base_d1: Any) -> None:
    async with _d1_corriendo(base_d1) as supervisor:
        await _insertar(base_d1, CASO_Y_CONTRASTE)
        await _procesada_hasta(base_d1, "TXN-004")
        fila = supervisor.informe().tarea(NOMBRE_D1)

    assert await _alertas_por_transaccion(base_d1) == {"TXN-001": 1}, (
        "solo TXN-001 supera el umbral; TXN-002..004 son el contraste"
    )
    alerta = await base_d1["alertas"].find_one({"transaccion_id": "TXN-001"})
    assert alerta["_id"] == "ALR-001"
    assert alerta["puntaje"] == 75
    assert alerta["severidad"] == "CRITICA"
    # H-26: v2. Tres reglas embebidas con version, peso y la explicacion del evaluador,
    # y la politica usada; el validador real de `alertas` las acepto al insertar.
    assert [
        (r["regla_id"], r["codigo"], r["version"], r["peso"])
        for r in alerta["reglas_disparadas"]
    ] == [("REG-001", "R-01", 1, 35), ("REG-002", "R-03", 1, 25), ("REG-003", "R-05", 1, 15)]
    assert all(r["explicacion"] for r in alerta["reglas_disparadas"])
    assert alerta["politica"] == {"politica_id": "POL-001", "version": 1}
    assert alerta["estado"] == "nueva"
    assert alerta["cliente_nombre"] == "Maria Rodriguez Vargas"
    assert (alerta["cuenta_origen"], alerta["cuenta_destino"]) == ("8712-4455", "6033-9001")
    assert alerta["esquema_version"] == 2
    assert "POL-001 v1" in alerta["historial"][0]["nota"]
    # El dinero sale como long de 64 bits, no como int32 ni double.
    assert await base_d1["alertas"].count_documents(
        {"_id": "ALR-001", "monto_crc": {"$type": "long", "$eq": 280_000}}
    ) == 1
    assert fila.estado == "corriendo"
    assert fila.ultimo_error is None


# ---------------------------------------------------------------------------
# Resiliencia
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_reanuda_desde_el_token_sin_duplicar_ni_perder(base_d1: Any) -> None:
    # 1. D1 arriba procesa el demo.
    async with _d1_corriendo(base_d1):
        await _insertar(base_d1, CASO_Y_CONTRASTE)
        await _procesada_hasta(base_d1, "TXN-004")
    token_tras_demo = await base_d1["deteccion_estado"].find_one({"_id": NOMBRE_D1})

    # 2. D1 abajo: entran transacciones que nadie esta mirando.
    lote = [
        _noche(26, "TXN-010", "7001-0010"),
        _oficina(26, "TXN-011"),
        _noche(27, "TXN-012", "7001-0012"),
        _oficina(27, "TXN-013"),
        _noche(28, "TXN-014", "7001-0014"),
        _oficina(28, "TXN-015"),
    ]
    await _insertar(base_d1, lote)
    assert await base_d1["alertas"].count_documents({}) == 1, "con D1 abajo no nace nada"

    # 3. D1 vuelve: reanuda desde el token y no pierde ninguna.
    async with _d1_corriendo(base_d1):
        await _procesada_hasta(base_d1, "TXN-015")

    esperadas = {"TXN-001": 1, "TXN-010": 1, "TXN-012": 1, "TXN-014": 1}
    assert await _alertas_por_transaccion(base_d1) == esperadas
    for transaccion_id in ("TXN-010", "TXN-012", "TXN-014"):
        alerta = await base_d1["alertas"].find_one({"transaccion_id": transaccion_id})
        assert alerta["_id"] == transaccion_id.replace("TXN-", "ALR-")
        assert alerta["puntaje"] == 75
    antes = {
        documento["_id"]: documento["fecha_creacion"]
        async for documento in base_d1["alertas"].find({})
    }

    # 4. El proceso "murio" entre la alerta y el token: el token quedo atras. D1
    #    reprocesa TXN-010..015 y el indice unico rechaza cada duplicado.
    await base_d1["deteccion_estado"].replace_one({"_id": NOMBRE_D1}, token_tras_demo)
    async with _d1_corriendo(base_d1) as supervisor:
        await _procesada_hasta(base_d1, "TXN-015")
        fila = supervisor.informe().tarea(NOMBRE_D1)

    assert await _alertas_por_transaccion(base_d1) == esperadas
    despues = {
        documento["_id"]: documento["fecha_creacion"]
        async for documento in base_d1["alertas"].find({})
    }
    assert despues == antes, "el reproceso no reescribe alertas existentes"
    assert fila.ultimo_error is None, "un reproceso es funcionamiento normal, no un error"
    assert await base_d1["deteccion_estado"].count_documents({"clase": "descarte"}) == 0


@pytest.mark.asyncio
async def prueba_un_token_inservible_resincroniza_y_sigue_detectando(base_d1: Any) -> None:
    await RepoDeteccion(base_d1).guardar_token(
        NOMBRE_D1, {"_data": "82DEADBEEF"}, transaccion_id=None
    )

    async with _d1_corriendo(base_d1) as supervisor:
        error = supervisor.informe().tarea(NOMBRE_D1).ultimo_error
        await _insertar(base_d1, [_noche(29, "TXN-020", "7001-0020")])
        await _procesada_hasta(base_d1, "TXN-020")

    assert error is not None and "token de reanudacion" in error.mensaje
    assert await _alertas_por_transaccion(base_d1) == {"TXN-020": 1}
    estado = await base_d1["deteccion_estado"].find_one({"_id": NOMBRE_D1})
    assert estado["token_reanudacion"] != {"_data": "82DEADBEEF"}


@pytest.mark.asyncio
async def prueba_el_indice_unico_frena_el_duplicado_aunque_el_id_sea_otro(
    base_d1: Any,
) -> None:
    """La alerta de TXN-001 ya existe con otro `_id` (la escribio otro proceso).

    El `_id` derivado (ALR-001) esta libre, asi que lo unico que impide la segunda
    alerta es el indice unico de `transaccion_id`. Es la prueba de que la garantia es
    de la base y no del identificador que elige D1.
    """
    alerta_previa = DATOS_DEMO["alertas"][0].model_copy(update={"alerta_id": "ALR-900"})
    await base_d1["alertas"].insert_one(a_entero_64(alerta_previa.a_documento()))

    async with _d1_corriendo(base_d1) as supervisor:
        await _insertar(base_d1, TRANSACCIONES[:1])
        await _procesada_hasta(base_d1, "TXN-001")
        fila = supervisor.informe().tarea(NOMBRE_D1)

    assert await _alertas_por_transaccion(base_d1) == {"TXN-001": 1}
    assert await base_d1["alertas"].count_documents({"_id": "ALR-001"}) == 0
    assert fila.ultimo_error is None
    assert await base_d1["deteccion_estado"].count_documents({"clase": "descarte"}) == 0
