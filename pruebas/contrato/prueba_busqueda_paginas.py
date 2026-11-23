"""H-19: pantalla `/busqueda` y exportacion CSV, con el repositorio sustituido por un doble."""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_repo_busqueda
from app.main import crear_aplicacion
from pruebas.contrato.prueba_busqueda_api import ALERTAS, TRANSACCIONES
from pruebas.dobles.repo_busqueda_falso import RepoBusquedaFalso


@pytest.fixture
def repo() -> RepoBusquedaFalso:
    return RepoBusquedaFalso(TRANSACCIONES, ALERTAS)


@pytest_asyncio.fixture
async def cliente(repo: RepoBusquedaFalso) -> AsyncIterator[AsyncClient]:
    aplicacion = crear_aplicacion()
    aplicacion.dependency_overrides[dep_repo_busqueda] = lambda: repo
    async with (
        LifespanManager(aplicacion) as gestionada,
        AsyncClient(
            transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
        ) as http,
    ):
        yield http


@pytest.mark.asyncio
async def prueba_la_pantalla_reemplaza_la_maqueta(cliente: AsyncClient) -> None:
    respuesta = await cliente.get("/busqueda")
    assert respuesta.status_code == 200
    assert "6 resultados" in respuesta.text
    assert "TXN-006" in respuesta.text
    assert "en construccion" not in respuesta.text.lower()


@pytest.mark.asyncio
async def prueba_filtros_combinables_en_la_pantalla(cliente: AsyncClient) -> None:
    respuesta = await cliente.get(
        "/busqueda",
        params={"cuenta": "CTA-002", "estado_alerta": "", "monto_max": "20000"},
    )
    assert "1 resultados" in respuesta.text and "TXN-002" in respuesta.text
    assert "TXN-004" not in respuesta.text
    assert 'value="CTA-002"' in respuesta.text


@pytest.mark.asyncio
async def prueba_campos_en_blanco_del_formulario_no_son_error(
    cliente: AsyncClient,
) -> None:
    respuesta = await cliente.get(
        "/busqueda",
        params={
            "desde": "",
            "hasta": "",
            "monto_min": "",
            "monto_max": "",
            "cuenta": "",
            "canal": "",
            "estado_alerta": "",
            "limite": "50",
        },
    )
    assert respuesta.status_code == 200 and "6 resultados" in respuesta.text


@pytest.mark.asyncio
async def prueba_el_dia_del_formulario_es_hora_de_costa_rica(
    cliente: AsyncClient, repo: RepoBusquedaFalso
) -> None:
    await cliente.get(
        "/busqueda", params={"desde": "2026-10-03", "hasta": "2026-10-03"}
    )
    filtro = repo.llamadas[-1]["filtro"]
    assert filtro.desde.isoformat() == "2026-10-03T00:00:00-06:00"
    assert filtro.hasta.isoformat() == "2026-10-03T23:59:59.999999-06:00"


@pytest.mark.asyncio
async def prueba_paginacion_conserva_los_filtros(cliente: AsyncClient) -> None:
    respuesta = await cliente.get(
        "/busqueda", params={"cuenta": "8712-4455", "limite": "2"}
    )
    assert "desplazamiento=2" in respuesta.text
    assert "cuenta=8712-4455" in respuesta.text
    segunda = await cliente.get(
        "/busqueda",
        params={"cuenta": "8712-4455", "limite": "2", "desplazamiento": "2"},
    )
    assert "TXN-004" in segunda.text and "TXN-006" not in segunda.text
    assert "desplazamiento=0" in segunda.text  # enlace a la pagina anterior


@pytest.mark.asyncio
async def prueba_un_monto_con_decimales_se_muestra_como_error_y_no_consulta(
    cliente: AsyncClient, repo: RepoBusquedaFalso
) -> None:
    respuesta = await cliente.get("/busqueda", params={"monto_min": "1500.50"})
    assert respuesta.status_code == 422
    assert "entero en colones" in respuesta.text
    assert repo.llamadas == []


@pytest.mark.asyncio
async def prueba_depurar_muestra_el_plan(cliente: AsyncClient) -> None:
    respuesta = await cliente.get("/busqueda", params={"depurar": "on"})
    assert "Plan de ejecucion" in respuesta.text


@pytest.mark.asyncio
async def prueba_el_csv_aplica_los_mismos_filtros_con_montos_enteros_y_hora_cr(
    cliente: AsyncClient,
) -> None:
    respuesta = await cliente.get(
        "/busqueda/exportar.csv", params={"cuenta": "CTA-002", "monto_min": "30000"}
    )
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/csv")
    assert "attachment" in respuesta.headers["content-disposition"]
    filas = list(csv.reader(io.StringIO(respuesta.text)))
    assert filas[0][:3] == ["transaccion_id", "fecha_hora_cr", "cuenta_origen"]
    assert [f[0] for f in filas[1:]] == ["TXN-004"]
    fila = dict(zip(filas[0], filas[1], strict=True))
    assert fila["monto_crc"] == "40000"  # entero, sin separador ni decimales
    assert fila["fecha_hora_cr"] == "2026-10-04 06:00:00"  # 12:00 UTC = 06:00 en CR
    assert respuesta.headers["x-filas-exportadas"] == "1"


@pytest.mark.asyncio
async def prueba_el_csv_con_filtro_invalido_es_422_del_contrato(
    cliente: AsyncClient,
) -> None:
    respuesta = await cliente.get("/busqueda/exportar.csv", params={"monto_max": "9.5"})
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["codigo"] == "parametros_invalidos"
    rango = await cliente.get(
        "/busqueda/exportar.csv", params={"desde": "2026-10-05", "hasta": "2026-10-01"}
    )
    assert rango.status_code == 422


@pytest.mark.asyncio
async def prueba_el_csv_con_alerta_incluye_su_estado(cliente: AsyncClient) -> None:
    respuesta = await cliente.get(
        "/busqueda/exportar.csv", params={"estado_alerta": "confirmada"}
    )
    filas = list(csv.DictReader(io.StringIO(respuesta.text)))
    assert [
        (f["transaccion_id"], f["alerta_id"], f["alerta_estado"]) for f in filas
    ] == [("TXN-005", "ALR-002", "confirmada")]
