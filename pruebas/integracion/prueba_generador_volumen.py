"""H-04: 100.000 transacciones sin degradarse, con el tiempo reportado.

Marcada `volumen` ademas de `mongo`. Corre en la compuerta de Mongo: son segundos, y
reporta el tiempo para que una regresion de rendimiento se note.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.generador.ejecucion import ejecutar
from app.nucleo.config import AjustesGenerador
from app.repos.esquemas import obtener_validadores, recrear_con_validador
from app.repos.generador import RepoGenerador
from pruebas.conftest import BASE_SEMILLA, saltar_o_fallar

pytestmark = [pytest.mark.mongo, pytest.mark.volumen]

# Piso de rendimiento, muy por debajo de lo medido: detecta una degradacion de orden de
# magnitud (por ejemplo, volver a insertar documento por documento), no ruido de la maquina.
PISO_DOCUMENTOS_POR_SEGUNDO = 2_000


@pytest.mark.asyncio
async def prueba_cien_mil_transacciones_con_el_validador_activo(
    base_mongo: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    validadores = await obtener_validadores(base_mongo.client[BASE_SEMILLA])
    for coleccion in ("clientes", "cuentas", "perfiles_comportamiento", "transacciones"):
        if coleccion not in validadores:
            saltar_o_fallar(f"antifraude.{coleccion} no tiene validador")
        await recrear_con_validador(base_mongo, coleccion, validadores[coleccion])

    resumen = await ejecutar(RepoGenerador(base_mongo), AjustesGenerador(clientes=1000, transacciones=100_000)
    )

    with capsys.disabled():
        print(
            f"\n[volumen] {resumen.transacciones} transacciones en {resumen.segundos:.1f} s "
            f"({resumen.documentos_por_segundo:.0f} docs/s), fraude {resumen.total_fraude}"
        )
    assert await base_mongo.transacciones.count_documents({}) == 100_000
    assert resumen.total_fraude < 0.02 * 100_000
    assert resumen.documentos_por_segundo > PISO_DOCUMENTOS_POR_SEGUNDO
