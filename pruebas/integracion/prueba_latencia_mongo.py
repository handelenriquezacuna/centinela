"""H-09 · Latencia de deteccion medida contra MongoDB de verdad.

La hora de insercion sale del evento del change stream (`wallTime`), no de
`Transaccion.fecha`: las transacciones de estas pruebas llevan fecha de septiembre de
2026, es decir, historica. Si D1 midiera contra `fecha`, la latencia saldria de dias o
de milisegundos negativos, y el tope de abajo no se cumpliria.

No se afirma un umbral fragil: el tope es holgado (5 s) y lo que se verifica de verdad es
la forma de la medicion (n crece, p50 <= p95 <= max). La cifra medida en una maquina
concreta esta en docs/06-plataforma.md.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.deteccion.d1 import NOMBRE_D1
from pruebas.integracion.prueba_d1_mongo import (  # noqa: F401 - base_d1 es un fixture
    _d1_corriendo,
    _esperar,
    _insertar,
    _noche,
    _oficina,
    _procesada_hasta,
    base_d1,
)

pytestmark = pytest.mark.mongo

TOPE_HOLGADO_MS = 5000.0


def _lote(desde: int, cuantas: int) -> list[Any]:
    """`cuantas` transferencias nocturnas grandes, cada una a un destino nuevo."""
    return [
        _noche(1 + (desde + i) % 27, f"TXN-{desde + i:03d}", f"7100-{desde + i:04d}")
        for i in range(cuantas)
    ]


@pytest.mark.asyncio
async def prueba_n_crece_y_la_latencia_es_razonable(base_d1: Any) -> None:  # noqa: F811
    async with _d1_corriendo(base_d1) as supervisor:
        antes = supervisor.informe().tarea(NOMBRE_D1).latencia
        assert antes is not None and antes.n == 0

        primeras = _lote(100, 3)
        await _insertar(base_d1, primeras)
        await _procesada_hasta(base_d1, primeras[-1].transaccion_id)
        tras_tres = supervisor.informe().tarea(NOMBRE_D1).latencia

        # Una transaccion sin alerta no agrega medicion: no hay alerta que medir.
        resto = _lote(110, 7)
        await _insertar(base_d1, [_oficina(26, "TXN-109"), *resto])
        await _procesada_hasta(base_d1, resto[-1].transaccion_id)
        tras_diez = supervisor.informe().tarea(NOMBRE_D1).latencia

    assert tras_tres.n == 3
    assert tras_diez.n == 10
    assert await base_d1["alertas"].count_documents({}) == 10
    assert 0 <= tras_diez.p50 <= tras_diez.p95 <= tras_diez.max
    assert tras_diez.p95 < TOPE_HOLGADO_MS, f"p95 = {tras_diez.p95} ms"
    assert tras_diez.max < TOPE_HOLGADO_MS * 2, f"max = {tras_diez.max} ms"


@pytest.mark.asyncio
async def prueba_estado_publica_la_latencia_de_d1(base_d1: Any) -> None:  # noqa: F811
    """El informe que arma `/estado` lleva la misma ventana que mide D1."""
    from app.api.esquemas import EstadoFuncional

    async with _d1_corriendo(base_d1) as supervisor:
        lote = _lote(200, 4)
        await _insertar(base_d1, lote)
        await _procesada_hasta(base_d1, lote[-1].transaccion_id)

        async def cuatro() -> bool:
            medida = supervisor.informe().tarea(NOMBRE_D1).latencia
            return medida is not None and medida.n == 4

        await _esperar(cuatro, "las cuatro mediciones de latencia")
        estado = EstadoFuncional.desde_informe(
            supervisor.informe(), version="prueba", entorno="prueba"
        )

    d1 = next(tarea for tarea in estado.tareas if tarea.nombre == NOMBRE_D1)
    assert d1.latencia_ms is not None
    assert d1.latencia_ms.n == 4
    assert d1.latencia_ms.p95 is not None and d1.latencia_ms.p95 < TOPE_HOLGADO_MS
