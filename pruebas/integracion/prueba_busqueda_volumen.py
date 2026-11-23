"""H-15: menos de 200 ms sobre 100.000 transacciones, medido como p95.

Marcada `mongo` y `volumen`. Genera 2.000 clientes y 100.000 transacciones en una base
propia con los indices reales, siembra alertas, y mide 20 consultas representativas
por la API completa (ruta, repositorio y Mongo; sin red, porque la app corre en proceso).
Cada consulta se repite `REPETICIONES` veces despues de una pasada de calentamiento; el
p95 se calcula sobre todas las muestras.

Imprime el p95 global y el de cada consulta, para que un indice faltante se vea por
nombre y no como un promedio enganoso.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.main import crear_aplicacion
from app.nucleo.config import Ajustes, AjustesGenerador, obtener_ajustes
from pruebas.conftest import MENSAJE_SIN_MONGO, _mongo_responde, saltar_o_fallar
from pruebas.integracion.prueba_busqueda_mongo import preparar_base

pytestmark = [pytest.mark.mongo, pytest.mark.volumen]

BASE_VOLUMEN = "antifraude_pruebas_busqueda_volumen"
REPETICIONES = 5
LIMITE_P95_MS = 200.0

# (nombre, parametros). Cubre cada filtro solo y combinado, como lo usa un analista.
CONSULTAS: list[tuple[str, dict[str, Any]]] = [
    ("cuenta_cta", {"cuenta": "CTA-0100"}),
    ("cuenta_cta_otra", {"cuenta": "CTA-1500"}),
    ("cuenta_sinpe", {"cuenta": "8712-4455"}),
    ("cuenta_destino", {"cuenta": "6033-9001"}),
    ("cuenta_y_fecha", {"cuenta": "CTA-0100", "desde": "2026-09-01T00:00:00Z"}),
    ("cuenta_y_monto", {"cuenta": "CTA-0100", "monto_min": 50000}),
    ("monto_minimo", {"monto_min": 100000}),
    ("monto_rango_angosto", {"monto_min": 90000, "monto_max": 100000}),
    ("monto_rango_ancho", {"monto_min": 10000, "monto_max": 250000}),
    ("monto_y_fecha", {"monto_min": 100000, "desde": "2026-10-15T00:00:00Z"}),
    ("estado_nueva", {"estado_alerta": "nueva"}),
    ("estado_confirmada", {"estado_alerta": "confirmada"}),
    ("estado_y_monto", {"estado_alerta": "escalada", "monto_min": 100000}),
    ("estado_y_cuenta", {"estado_alerta": "nueva", "cuenta": "CTA-0100"}),
    ("fecha_rango_dia", {"desde": "2026-10-10T00:00:00Z", "hasta": "2026-10-10T23:59:59Z"}),
    ("fecha_desde", {"desde": "2026-11-15T00:00:00Z"}),
    ("canal", {"canal": "sinpe_movil"}),
    ("sin_filtros", {}),
    ("pagina_profunda", {"monto_min": 10000, "desplazamiento": 400, "limite": 50}),
    ("todo_combinado", {
        "cuenta": "CTA-0100", "desde": "2026-09-01T00:00:00Z", "monto_min": 1000,
        "canal": "sinpe_movil", "estado_alerta": "nueva",
    }),
]


def percentil(muestras: list[float], p: float) -> float:
    ordenadas = sorted(muestras)
    return ordenadas[max(math.ceil(p * len(ordenadas)) - 1, 0)]


@pytest_asyncio.fixture
async def cliente_volumen() -> AsyncIterator[AsyncClient]:
    from app.nucleo.db import ciclo_cliente

    ajustes = Ajustes(mongo={"uri": Ajustes().mongo.uri, "base": BASE_VOLUMEN})
    if not await _mongo_responde(ajustes):
        saltar_o_fallar(MENSAJE_SIN_MONGO)
    async with ciclo_cliente(ajustes) as mongo:
        try:
            await preparar_base(
                mongo, BASE_VOLUMEN, AjustesGenerador(clientes=2000, transacciones=100_000)
            )
            os.environ["CENTINELA_MONGO__BASE"] = BASE_VOLUMEN
            obtener_ajustes.cache_clear()
            async with LifespanManager(crear_aplicacion()) as gestionada, AsyncClient(
                transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
            ) as http:
                yield http
        finally:
            os.environ.pop("CENTINELA_MONGO__BASE", None)
            obtener_ajustes.cache_clear()
            await mongo.drop_database(BASE_VOLUMEN)


@pytest.mark.asyncio
async def prueba_p95_de_veinte_consultas_bajo_200_ms_sobre_cien_mil(
    cliente_volumen: AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    total = (await cliente_volumen.get("/api/v1/transacciones", params={"limite": 1})).json()
    assert total["total"] == 100_000

    todas: list[float] = []
    por_consulta: dict[str, list[float]] = {}
    for nombre, parametros in CONSULTAS:
        await cliente_volumen.get("/api/v1/transacciones", params=parametros)  # calentamiento
        muestras: list[float] = []
        for _ in range(REPETICIONES):
            inicio = time.perf_counter()
            respuesta = await cliente_volumen.get("/api/v1/transacciones", params=parametros)
            muestras.append((time.perf_counter() - inicio) * 1000)
            assert respuesta.status_code == 200, (nombre, respuesta.text)
        por_consulta[nombre] = muestras
        todas.extend(muestras)

    p95 = percentil(todas, 0.95)
    with capsys.disabled():
        print(f"\n[volumen] p95 global = {p95:.1f} ms sobre {len(todas)} muestras "
              f"({len(CONSULTAS)} consultas x {REPETICIONES})")
        for nombre, muestras in por_consulta.items():
            print(f"[volumen]   {nombre:22s} p95 {percentil(muestras, 0.95):7.1f} ms  "
                  f"max {max(muestras):7.1f} ms")
    lentas = {n: round(percentil(m, 0.95), 1) for n, m in por_consulta.items()
              if percentil(m, 0.95) >= LIMITE_P95_MS}
    assert p95 < LIMITE_P95_MS, f"p95 {p95:.1f} ms; lentas: {lentas}"
