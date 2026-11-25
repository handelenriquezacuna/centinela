"""T-62 · D1 por lotes contra MongoDB de verdad.

Tres cosas, y la primera es la red de seguridad de todo el cambio:

1. **Equivalencia.** El mismo conjunto de transacciones, generado con semilla fija (rafagas
   del mismo origen, destinos repetidos, cuentas hermanas del mismo cliente, empates de
   fecha, insercion fuera de orden de fecha, listas de riesgo con fecha, rasgos de R-11),
   procesado evento por evento (`lote_maximo=1`, el camino de siempre) y por lotes de 7,
   de 200 y de 1000, produce exactamente las mismas alertas: mismos ids, puntajes,
   severidades y `reglas_disparadas` con sus explicaciones.
2. **Aislamiento.** Un evento que falla dentro de un lote va al descarte y el resto del lote
   se procesa.
3. **Caida a mitad de lote.** Con las alertas escritas y el token sin guardar, al reanudar
   no hay duplicados ni perdidas.

Para que la comparacion sea determinista, las transacciones ya estan todas en la base
cuando D1 las lee: se toma un token del change stream, se insertan, y D1 reanuda desde ese
token. Asi el evento por evento y el lote ven el mismo historial, sin una carrera entre la
insercion y la evaluacion.
"""

from __future__ import annotations

import asyncio
import random
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.deteccion.d1 import NOMBRE_D1, d1
from app.modelos import (
    ZONA_COSTA_RICA,
    Alerta,
    Cliente,
    Cuenta,
    EntradaListaRiesgo,
    HorarioHabitual,
    PerfilComportamiento,
    Transaccion,
)
from app.nucleo.config import AjustesDeteccion
from app.repos.carga_demo import a_entero_64, cargar_demo
from app.repos.deteccion import (
    OPCIONES_FLUJO_TRANSACCIONES,
    PIPELINE_TRANSACCIONES,
    RepoDeteccion,
)
from app.repos.rasgos_grafo import RepoRasgosGrafo
from app.supervision import Supervisor
from pruebas.integracion.prueba_d1_mongo import base_d1  # noqa: F401 - fixture

pytestmark = pytest.mark.mongo

LIMITE_SEGUNDOS = 60
CLIENTES = 14
TOTAL = 700
HISTORIAL = 160


def _ajustes(lote_maximo: int) -> AjustesDeteccion:
    return AjustesDeteccion(
        lote_maximo=lote_maximo,
        espera_cursor_ms=100,
        reintentos=1,
        espera_inicial_segundos=0.05,
        espera_maxima_segundos=0.2,
    )


# ---------------------------------------------------------------------------
# El conjunto de datos, con semilla fija
# ---------------------------------------------------------------------------


def _sinpe(numero: int) -> str:
    return f"{7000 + numero:04d}-{1000 + numero:04d}"


def _identidad() -> tuple[list[Cliente], list[Cuenta], list[PerfilComportamiento]]:
    """14 clientes; el primero con dos cuentas; los dos ultimos sin perfil materializado."""
    registro = datetime(2026, 1, 1, tzinfo=UTC)
    clientes: list[Cliente] = []
    cuentas: list[Cuenta] = []
    perfiles: list[PerfilComportamiento] = []
    for n in range(1, CLIENTES + 1):
        cliente_id = f"CLI-{n + 100}"
        clientes.append(
            Cliente(
                _id=cliente_id,
                nombre=f"Cliente de prueba {n}",
                cedula=f"{n % 10}-0000-{n:04d}",
                fecha_registro=registro,
            )
        )
        cuentas.append(
            Cuenta(
                _id=f"CTA-{n + 100}",
                cuenta_sinpe=_sinpe(n),
                cliente_id=cliente_id,
                entidad="Entidad A",
            )
        )
        if n == 1:
            cuentas.append(
                Cuenta(
                    _id="CTA-151",
                    cuenta_sinpe=_sinpe(51),
                    cliente_id=cliente_id,
                    entidad="Entidad B",
                )
            )
        if n <= CLIENTES - 2:
            perfiles.append(
                PerfilComportamiento(
                    _id=cliente_id,
                    promedio_crc=15_000 + 4_000 * n,
                    maximo_crc=90_000 + 10_000 * n,
                    num_transferencias=20 + n,
                    horario_habitual=HorarioHabitual(inicio="07:00", fin="19:00"),
                    destinos_frecuentes=[_sinpe(1 + n % 5)],
                )
            )
    return clientes, cuentas, perfiles


DESTINOS_EXTERNOS = [f"6{n:03d}-9{n:03d}" for n in range(1, 9)]
MULA = DESTINOS_EXTERNOS[0]
CUENTAS_ORIGEN = [f"CTA-{n + 100}" for n in range(1, CLIENTES + 1)] + ["CTA-151"]
SINPE_DE = {f"CTA-{n + 100}": _sinpe(n) for n in range(1, CLIENTES + 1)} | {"CTA-151": _sinpe(51)}
INICIO = datetime(2026, 9, 26, 4, 0, tzinfo=UTC)


def _transacciones(azar: random.Random) -> tuple[list[Transaccion], list[Transaccion]]:
    """(historial previo al change stream, transacciones del stream en orden de insercion).

    El stream es casi cronologico pero no del todo (algunas transacciones llegan con fecha
    anterior a las ya insertadas), con rafagas del mismo origen, empates exactos de fecha y
    destinos que se repiten dentro y fuera del lote.
    """
    numero = 0

    def nueva(origen: str, destino: str, fecha: datetime, monto: int) -> Transaccion:
        nonlocal numero
        numero += 1
        return Transaccion(
            _id=f"TXN-{numero:06d}",
            cuenta_origen=origen,
            cuenta_destino=destino,
            sinpe_origen=SINPE_DE[origen],
            monto_crc=monto,
            fecha=fecha,
        )

    def destino() -> str:
        # Mucha repeticion: pocos destinos externos, y a veces la cuenta de otro cliente.
        if azar.random() < 0.15:
            return _sinpe(azar.randint(1, CLIENTES))
        return azar.choice(DESTINOS_EXTERNOS[:5] + [MULA, MULA])

    def monto() -> int:
        return azar.choice([8_000, 15_000, 30_000, 60_000, 120_000, 350_000, 780_000])

    historial: list[Transaccion] = []
    for _ in range(HISTORIAL):
        origen = azar.choice(CUENTAS_ORIGEN)
        fecha = INICIO - timedelta(days=azar.randint(1, 120), minutes=azar.randint(0, 1_400))
        historial.append(nueva(origen, destino(), fecha, monto()))

    stream: list[Transaccion] = []
    reloj = INICIO
    while len(stream) < TOTAL:
        reloj += timedelta(seconds=azar.randint(0, 150))
        origen = azar.choice(CUENTAS_ORIGEN)
        # Mitad de las veces el mismo destino, para que R-03 deje de ver un destino nuevo
        # dentro del mismo lote.
        elegido = destino()
        stream.append(nueva(origen, elegido, reloj, monto()))
        if azar.random() < 0.3:
            for _ in range(azar.randint(1, 5)):
                desfase = timedelta(seconds=azar.choice([0, 0, 30, 90, 300, 900]))
                stream.append(
                    nueva(
                        origen,
                        elegido if azar.random() < 0.6 else destino(),
                        reloj + desfase,
                        monto(),
                    )
                )
    stream = stream[:TOTAL]
    # Fuera de orden: algunas transacciones se insertan despues de otras mas nuevas.
    for _ in range(40):
        i = azar.randrange(len(stream) - 3)
        stream[i], stream[i + azar.randint(1, 3)] = stream[i + 1], stream[i]
    # Los ids siguen el orden de insercion, como en produccion.
    renumeradas = [
        t.model_copy(update={"transaccion_id": f"TXN-{HISTORIAL + 1 + i:06d}"})
        for i, t in enumerate(stream)
    ]
    return historial, renumeradas


async def _sembrar(base: Any) -> tuple[list[Transaccion], Any]:
    """Identidad, listas, historial y rasgos. Devuelve el stream y el token de arranque."""
    clientes, cuentas, perfiles = _identidad()
    await cargar_demo(
        base, {"clientes": clientes, "cuentas": cuentas, "perfiles_comportamiento": perfiles}
    )
    # R-07: una lista con la cuenta mula reportada a mitad de la serie, para que unas
    # transacciones la vean vigente y otras no (el filtro es por la fecha de cada una).
    await cargar_demo(
        base,
        {
            "listas_riesgo": [
                EntradaListaRiesgo(
                    _id="RSK-901",
                    cuenta=MULA,
                    motivo="Reportada durante la serie",
                    fecha_reporte=INICIO + timedelta(hours=3),
                ),
                EntradaListaRiesgo(
                    _id="RSK-902",
                    cuenta=DESTINOS_EXTERNOS[1],
                    motivo="Reportada antes de la serie",
                    fecha_reporte=INICIO - timedelta(days=2),
                ),
            ]
        },
    )
    historial, stream = _transacciones(random.Random(62))
    await base["transacciones"].insert_many([a_entero_64(t.a_documento()) for t in historial])

    # Token de arranque: todo lo que se inserte despues entra al change stream.
    flujo = await base["transacciones"].watch(
        PIPELINE_TRANSACCIONES, max_await_time_ms=100, **OPCIONES_FLUJO_TRANSACCIONES
    )
    async with flujo:
        await flujo.try_next()
        token = dict(flujo.resume_token)
    await base["transacciones"].insert_many([a_entero_64(t.a_documento()) for t in stream])

    # R-11: rasgos de grafo reales sobre todo lo insertado. La mula recibe de muchos y
    # sus destinos externos son cuentas de clientes, que reenvian.
    corte = max(t.fecha for t in stream) + timedelta(minutes=1)
    await RepoRasgosGrafo(base).recalcular(
        corte, ventana_minutos=15, horizonte=timedelta(days=400)
    )
    return stream, token


# ---------------------------------------------------------------------------
# D1 reanudando desde el token
# ---------------------------------------------------------------------------


async def _esperar(condicion: Callable[[], Awaitable[bool]], que: str) -> None:
    limite = asyncio.get_running_loop().time() + LIMITE_SEGUNDOS
    while not await condicion():
        if asyncio.get_running_loop().time() > limite:
            raise AssertionError(f"pasaron {LIMITE_SEGUNDOS} s esperando {que}")
        await asyncio.sleep(0.05)


class RepoContado(RepoDeteccion):
    """El repositorio real, contando cuantas veces se guarda el token y se escribe."""

    def __init__(self, base: Any) -> None:
        super().__init__(base)
        self.tokens = 0
        self.lotes_escritos: list[int] = []
        self.por_evento = 0
        self.fallar_el_token = 0

    async def guardar_token(self, *args: Any, **kwargs: Any) -> None:
        if self.fallar_el_token > 0:
            self.fallar_el_token -= 1
            raise ConnectionError("el proceso murio antes de guardar el token")
        self.tokens += 1
        await super().guardar_token(*args, **kwargs)

    async def insertar_alertas(self, alertas: Sequence[Alerta]) -> dict[str, bool | Exception]:
        escritas = await super().insertar_alertas(alertas)
        self.lotes_escritos.append(sum(1 for nueva in escritas.values() if nueva is True))
        return escritas

    async def insertar_alerta(self, alerta: Alerta) -> bool:
        self.por_evento += 1
        return await super().insertar_alerta(alerta)


@asynccontextmanager
async def _d1_reanudando(
    base: Any, token: Any, lote_maximo: int, repo: RepoContado | None = None
) -> AsyncIterator[tuple[Supervisor, RepoContado]]:
    """D1 con el token ya guardado: reprocesa lo que se inserto despues de ese token."""
    repo = repo or RepoContado(base)
    await RepoDeteccion(base).guardar_token(NOMBRE_D1, token, transaccion_id=None)
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(repo, supervisor, ajustes=_ajustes(lote_maximo)))
    try:
        yield supervisor, repo
    finally:
        await supervisor.detener()


async def _hasta(base: Any, transaccion_id: str) -> None:
    async def llego() -> bool:
        estado = await base["deteccion_estado"].find_one({"_id": NOMBRE_D1})
        return estado is not None and estado.get("ultima_transaccion_id") == transaccion_id

    await _esperar(llego, f"que D1 guarde el token de {transaccion_id}")


def _forma(alerta: dict[str, Any]) -> dict[str, Any]:
    """La alerta sin lo que cambia entre corridas: la hora en que se creo."""
    copia = {k: v for k, v in alerta.items() if k != "fecha_creacion"}
    copia["historial"] = [
        {k: v for k, v in entrada.items() if k != "fecha"} for entrada in alerta["historial"]
    ]
    return copia


async def _alertas(base: Any) -> dict[str, dict[str, Any]]:
    return {
        alerta["transaccion_id"]: _forma(alerta)
        async for alerta in base["alertas"].find({}).sort("_id", 1)
    }


async def _vaciar(base: Any) -> None:
    await base["alertas"].delete_many({})
    await base["deteccion_estado"].delete_many({"clase": {"$in": ["reanudacion", "descarte"]}})


# ---------------------------------------------------------------------------
# 1. Equivalencia
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_por_lotes_produce_las_mismas_alertas_que_evento_por_evento(
    base_d1: Any,  # noqa: F811
) -> None:
    stream, token = await _sembrar(base_d1)
    ultimo = stream[-1].transaccion_id

    async with _d1_reanudando(base_d1, token, 1) as (_, repo_uno):
        await _hasta(base_d1, ultimo)
    esperadas = await _alertas(base_d1)
    assert repo_uno.tokens >= TOTAL, "evento por evento: un token por transaccion"
    assert repo_uno.lotes_escritos == [], "lote_maximo=1 usa el camino por evento"

    codigos = Counter(
        regla["codigo"] for alerta in esperadas.values() for regla in alerta["reglas_disparadas"]
    )
    # El conjunto tiene que ejercitar las reglas que dependen de la fecha de cada
    # transaccion y del orden dentro del lote, o la comparacion no prueba nada.
    assert len(esperadas) >= 100, f"muy pocas alertas ({len(esperadas)}) para comparar"
    assert {"R-01", "R-03", "R-05", "R-07", "R-09"} <= set(codigos), codigos
    assert any(
        regla["codigo"] == "R-03" for a in esperadas.values() for regla in a["reglas_disparadas"]
    )

    for lote_maximo in (7, 200, 1000):
        await _vaciar(base_d1)
        async with _d1_reanudando(base_d1, token, lote_maximo) as (_, repo):
            await _hasta(base_d1, ultimo)
        obtenidas = await _alertas(base_d1)

        assert obtenidas.keys() == esperadas.keys(), f"lote {lote_maximo}"
        for transaccion_id, esperada in esperadas.items():
            assert obtenidas[transaccion_id] == esperada, f"lote {lote_maximo}: {transaccion_id}"
        assert repo.tokens < TOTAL / 3, "por lotes el token se guarda una vez por lote"
        assert repo.lotes_escritos, "la escritura fue por insert_many"
        assert sum(repo.lotes_escritos) + repo.por_evento >= len(esperadas)
        assert await base_d1["deteccion_estado"].count_documents({"clase": "descarte"}) == 0


# ---------------------------------------------------------------------------
# 2. Aislamiento
# ---------------------------------------------------------------------------


def _con_origen_inexistente(transaccion: Transaccion, numero: int) -> Transaccion:
    return transaccion.model_copy(
        update={"transaccion_id": f"TXN-{numero:06d}", "cuenta_origen": "CTA-404"}
    )


@pytest.mark.asyncio
async def prueba_un_evento_envenenado_se_aisla_y_el_resto_del_lote_sigue(
    base_d1: Any,  # noqa: F811
) -> None:
    clientes, cuentas, perfiles = _identidad()
    await cargar_demo(
        base_d1,
        {"clientes": clientes, "cuentas": cuentas, "perfiles_comportamiento": perfiles},
    )
    flujo = await base_d1["transacciones"].watch(PIPELINE_TRANSACCIONES, max_await_time_ms=100)
    async with flujo:
        await flujo.try_next()
        token = dict(flujo.resume_token)

    fecha = datetime(2026, 9, 26, 23, 15, tzinfo=ZONA_COSTA_RICA)
    sanas = [
        Transaccion(
            _id=f"TXN-{n:06d}",
            cuenta_origen="CTA-102",
            cuenta_destino=f"6{n:03d}-0000",
            monto_crc=2_000_000,
            fecha=fecha + timedelta(minutes=n),
        )
        for n in (1, 2, 4, 5)
    ]
    # Sin cuenta de origen: no se puede armar su contexto. No es un fallo del lote.
    envenenada = _con_origen_inexistente(sanas[0], 3)
    lote = [sanas[0], sanas[1], envenenada, sanas[2], sanas[3]]
    await base_d1["transacciones"].insert_many([a_entero_64(t.a_documento()) for t in lote])

    async with _d1_reanudando(base_d1, token, 200) as (supervisor, repo):
        await _hasta(base_d1, "TXN-000005")
        fila = supervisor.informe().tarea(NOMBRE_D1)

    alertas = await _alertas(base_d1)
    assert set(alertas) == {"TXN-000001", "TXN-000002", "TXN-000004", "TXN-000005"}
    descartes = await base_d1["deteccion_estado"].find({"clase": "descarte"}).to_list(None)
    assert [d["transaccion_id"] for d in descartes] == ["TXN-000003"]
    assert "LookupError" in descartes[0]["motivo"]
    assert fila.ultimo_error is not None
    assert repo.tokens == 1, "un lote, un token, aun con un evento descartado"
    assert fila.eventos_procesados >= 5


# ---------------------------------------------------------------------------
# 3. Caida a mitad de lote
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_una_caida_con_las_alertas_escritas_y_sin_token_no_duplica_ni_pierde(
    base_d1: Any,  # noqa: F811
) -> None:
    stream, token = await _sembrar(base_d1)
    ultimo = stream[-1].transaccion_id

    async with _d1_reanudando(base_d1, token, 1):
        await _hasta(base_d1, ultimo)
    esperadas = await _alertas(base_d1)
    await _vaciar(base_d1)

    # La primera vez que D1 intenta guardar el token de un lote, el "proceso muere": las
    # alertas del lote ya estan escritas y el token no. D1 reabre desde el token anterior
    # y reprocesa el lote entero.
    repo = RepoContado(base_d1)
    repo.fallar_el_token = 1
    async with _d1_reanudando(base_d1, token, 200, repo) as (supervisor, _):
        await _hasta(base_d1, ultimo)
        fila = supervisor.informe().tarea(NOMBRE_D1)

    assert fila.ultimo_error is not None, "la caida queda registrada"
    assert await _alertas(base_d1) == esperadas, "ni una alerta de mas ni una de menos"
    assert await base_d1["alertas"].count_documents({}) == len(esperadas)
    # Cada alerta se inserto como nueva exactamente una vez: lo que el lote caido ya habia
    # escrito, el reproceso lo encontro hecho y el indice unico lo freno.
    assert repo.lotes_escritos[0] > 0
    assert sum(repo.lotes_escritos) == len(esperadas)
    assert await base_d1["deteccion_estado"].count_documents({"clase": "descarte"}) == 0
