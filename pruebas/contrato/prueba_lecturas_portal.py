"""Lecturas JSON del portal (T-61) con repositorios falsos. Sin Mongo.

Cada prueba compara la respuesta JSON con lo que muestra la pagina HTMX equivalente o con
la funcion pura que comparten: si las dos interfaces divergen, estas pruebas lo dicen.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import (
    dep_repo_alertas,
    dep_repo_casos_destino,
    dep_repo_detalle,
    dep_repo_reglas,
    dep_repo_tablero,
    dep_supervisor,
)
from app.api.graficos import disponer_grafo, graficos_tablero
from app.api.rutas.inteligencia import dep_repo_red_mulas
from app.api.rutas.listas_riesgo import dep_repo_listas_riesgo
from app.api.sankey import disponer_sankey
from app.deteccion.d1 import NOMBRE_D1
from app.main import crear_aplicacion
from app.repos.tablero import resumir
from app.supervision import Supervisor
from pruebas.dobles.casos_demo import RepoCasosFalso, caso_de_la_mula
from pruebas.dobles.catalogo_reglas import (
    politica_como_documento,
    reglas_como_documentos,
)
from pruebas.dobles.repo_detalle_falso import RepoDetalleFalso, alerta_v1, alerta_v2
from pruebas.dobles.repo_tablero_falso import (
    RepoListasRiesgoFalso,
    RepoRedMulasFalso,
    RepoTableroFalso,
    documentos_con_comparacion,
    red_de_cuatro_saltos,
)
from pruebas.dobles.repos_falsos import RepoAlertasFalso, RepoReglasFalso
from pruebas.sesiones import encabezados_de

Armar = Callable[..., Awaitable[AsyncClient]]


@pytest_asyncio.fixture
async def armar() -> AsyncIterator[Armar]:
    abiertos: list[Any] = []

    async def _armar(
        alertas: list[dict[str, Any]] | None = None,
        *,
        rol: str = "administrador",
        tablero: RepoTableroFalso | None = None,
        red: RepoRedMulasFalso | None = None,
        listas: RepoListasRiesgoFalso | None = None,
        supervisor: Supervisor | None = None,
    ) -> AsyncClient:
        repo = RepoAlertasFalso(alertas if alertas is not None else [alerta_v2()])
        creada = crear_aplicacion()
        creada.dependency_overrides[dep_repo_alertas] = lambda: repo
        creada.dependency_overrides[dep_repo_casos_destino] = lambda: RepoCasosFalso(repo)
        creada.dependency_overrides[dep_repo_detalle] = lambda: RepoDetalleFalso(repo.casos)
        reglas = RepoReglasFalso(reglas_como_documentos(), politica_como_documento())
        creada.dependency_overrides[dep_repo_reglas] = lambda: reglas
        creada.dependency_overrides[dep_repo_tablero] = lambda: tablero or RepoTableroFalso()
        creada.dependency_overrides[dep_repo_red_mulas] = lambda: red or RepoRedMulasFalso()
        creada.dependency_overrides[dep_repo_listas_riesgo] = lambda: listas or RepoListasRiesgoFalso()
        if supervisor is not None:
            creada.dependency_overrides[dep_supervisor] = lambda: supervisor
        gestor = LifespanManager(creada)
        gestionada = await gestor.__aenter__()
        http = AsyncClient(
            headers=encabezados_de(rol),
            transport=ASGITransport(app=gestionada.app),
            base_url="http://pruebas",
        )
        abiertos.append((gestor, http))
        return http

    yield _armar
    for gestor, http in abiertos:
        await http.aclose()
        await gestor.__aexit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pendientes", "atraso", "atrasada", "por_conteo"),
    [
        (0, 16.0, True, False),  # el conteo dice 0 pero D1 va 16 s detras: la red de seguridad
        (0, 15.0, False, False),
        (0, 0.0, False, False),
        (501, 0.0, True, True),
        (501, 60.0, True, True),
    ],
)
async def prueba_el_aviso_de_rezago_tambien_mira_el_atraso_en_segundos(
    armar: Armar,
    supervisor: Supervisor,
    pendientes: int,
    atraso: float,
    atrasada: bool,
    por_conteo: bool,
) -> None:
    supervisor.registrar_rezago(
        NOMBRE_D1,
        pendientes,
        None,
        datetime(2026, 11, 23, 18, 0, tzinfo=UTC),
        atraso_segundos=atraso,
    )
    http = await armar(supervisor=supervisor)
    aviso = (await http.get("/api/v1/deteccion/rezago")).json()

    assert aviso["atrasada"] is atrasada and aviso["por_conteo"] is por_conteo
    assert aviso["atraso_segundos"] == atraso and aviso["aviso_atraso_desde"] == 15.0


# ---------------------------------------------------------------------------
# Detalle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_detalle_v2_explica_cada_regla_y_suma_el_puntaje(armar: Armar) -> None:
    http = await armar()
    respuesta = await http.get("/api/v1/alertas/ALR-001/detalle")
    assert respuesta.status_code == 200
    detalle = respuesta.json()

    assert detalle["es_v2"] is True and detalle["aviso_v1"] is None
    assert detalle["alerta"]["_id"] == "ALR-001"
    filas = detalle["puntaje"]["filas"]
    assert [f["codigo"] for f in filas] == ["R-01", "R-03", "R-05"]
    assert [f["acumulado"] for f in filas] == [35, 60, 75]
    assert detalle["puntaje"]["suma"] == 75
    # La explicacion llega limpia, igual que en la pagina HTMX.
    assert filas[0]["explicacion"] == "Monto 750000 supera 10 veces el promedio 45200"
    alcanzado = [c for c in detalle["puntaje"]["cortes"] if c["alcanzado"]]
    assert [c["severidad"] for c in alcanzado] == ["CRITICA"]
    assert detalle["politica"] == {"politica_id": "POL-001", "version": 1, "origen": "usada"}


@pytest.mark.asyncio
async def prueba_el_detalle_trae_el_contexto_sin_la_etiqueta_oculta(armar: Armar) -> None:
    http = await armar()
    detalle = (await http.get("/api/v1/alertas/ALR-001/detalle")).json()

    transaccion = detalle["transaccion"]
    assert transaccion["transaccion_id"] == "TXN-001"
    assert transaccion["monto_crc"] == 750_000 and isinstance(transaccion["monto_crc"], int)
    assert "simulacion" not in transaccion and "_id" not in transaccion
    assert detalle["cliente"] == {
        "cliente_id": "CLI-001",
        "nombre": "Maria Rodriguez Vargas",
        "cedula": "1-1111-1111",
    }
    assert detalle["perfil"]["promedio_crc"] == 45_200
    assert detalle["perfil"]["horario_habitual"] == {"inicio": "07:00", "fin": "19:00"}
    assert detalle["nombres_agentes"]["AGT-001"] == "Jose Solis"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("estado", "esperados"),
    [
        ("nueva", ["en_revision"]),
        ("en_revision", ["confirmada", "descartada", "escalada"]),
        ("confirmada", []),
    ],
)
async def prueba_el_detalle_ofrece_solo_lo_que_la_maquina_permite(
    armar: Armar, estado: str, esperados: list[str]
) -> None:
    http = await armar([alerta_v2(estado, None if estado == "nueva" else "AGT-001")])
    acciones = (await http.get("/api/v1/alertas/ALR-001/detalle")).json()["acciones"]

    assert [a["destino"] for a in acciones] == esperados
    assert all(a["cierra"] for a in acciones if a["destino"] != "en_revision")


@pytest.mark.asyncio
async def prueba_el_detalle_v1_avisa_la_migracion(armar: Armar) -> None:
    http = await armar([alerta_v1()])
    detalle = (await http.get("/api/v1/alertas/ALR-001/detalle")).json()

    assert detalle["es_v2"] is False
    assert detalle["aviso_v1"] == "alerta v1: migrar con python tareas.py migrar"
    assert detalle["puntaje"]["filas"] == []


@pytest.mark.asyncio
async def prueba_el_detalle_de_una_alerta_que_no_existe_es_404_en_la_forma_del_error(
    armar: Armar,
) -> None:
    http = await armar()
    respuesta = await http.get("/api/v1/alertas/ALR-999/detalle")
    assert respuesta.status_code == 404
    assert respuesta.json()["error"]["codigo"] == "no_encontrado"
    assert (await http.get("/api/v1/alertas/no-es-id/detalle")).status_code == 422


# ---------------------------------------------------------------------------
# Casos por cuenta destino y rezago
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_los_casos_agrupan_por_destino_con_sugerencia_y_acciones(armar: Armar) -> None:
    http = await armar(caso_de_la_mula())
    pagina = (await http.get("/api/v1/casos-destino")).json()

    assert pagina["total"] == 2 and pagina["desplazamiento"] == 0
    mula = pagina["elementos"][0]
    assert mula["cuenta_destino"] == "6033-9001"
    assert mula["total_alertas"] == 3 and mula["victimas"] == 3
    assert mula["monto_total_crc"] == 520_000 and isinstance(mula["monto_total_crc"], int)
    assert mula["severidad_max"] == "CRITICA"
    assert [a["_id"] for a in mula["alertas"]] == ["ALR-101", "ALR-103", "ALR-102"]  # puntaje y luego la mas reciente
    assert mula["reglas"][0] == {"codigo": "REG-001", "veces": 3}
    assert "candidata" in mula["sugerencia"]
    assert [a["destino"] for a in mula["acciones"]] == ["en_revision"]


@pytest.mark.asyncio
async def prueba_un_caso_por_cuenta_y_404_cuando_ya_no_tiene_abiertas(armar: Armar) -> None:
    http = await armar(caso_de_la_mula())
    caso = await http.get("/api/v1/casos-destino/3390-6612")
    assert caso.status_code == 200 and caso.json()["total_alertas"] == 1

    vacio = await http.get("/api/v1/casos-destino/7777-0000")
    assert vacio.status_code == 404
    assert vacio.json()["error"]["codigo"] == "no_encontrado"
    assert (await http.get("/api/v1/casos-destino/no-es-cuenta")).status_code == 422


@pytest.mark.asyncio
async def prueba_el_limite_de_los_casos_se_acota_y_se_informa(armar: Armar) -> None:
    http = await armar(caso_de_la_mula())
    pagina = (await http.get("/api/v1/casos-destino", params={"limite": 100_000})).json()
    assert pagina["limite"] == 200  # app.pagina_maxima


async def _nunca_termina() -> None:
    await asyncio.sleep(3600)


@pytest_asyncio.fixture
async def supervisor() -> AsyncIterator[Supervisor]:
    creado = Supervisor(tolerancia_segundos=30)
    creado.lanzar(NOMBRE_D1, _nunca_termina())
    creado.latir(NOMBRE_D1)
    yield creado
    await creado.detener()


@pytest.mark.asyncio
@pytest.mark.parametrize(("pendientes", "atrasada"), [(501, True), (500, False), (0, False)])
async def prueba_el_aviso_de_rezago_respeta_el_umbral(
    armar: Armar, supervisor: Supervisor, pendientes: int, atrasada: bool
) -> None:
    supervisor.registrar_rezago(NOMBRE_D1, pendientes, 10.0, datetime(2026, 11, 23, 18, 0, tzinfo=UTC))
    http = await armar(supervisor=supervisor)
    aviso = (await http.get("/api/v1/deteccion/rezago")).json()

    assert aviso["atrasada"] is atrasada
    assert aviso["pendientes"] == pendientes and aviso["aviso_desde"] == 500


@pytest.mark.asyncio
async def prueba_sin_medicion_el_aviso_no_inventa_pendientes(
    armar: Armar, supervisor: Supervisor
) -> None:
    http = await armar(supervisor=supervisor)
    aviso = (await http.get("/api/v1/deteccion/rezago")).json()
    assert aviso == {
        "atrasada": False,
        "por_conteo": False,
        "pendientes": None,
        "aviso_desde": 500,
        "atraso_segundos": None,
        "aviso_atraso_desde": 15.0,
        "atraso_legible": "",
        "segundos_para_ponerse_al_dia": None,
        "estimado": "",
        "medido_en": None,
    }


# ---------------------------------------------------------------------------
# Tablero
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_tablero_vacio_dice_que_falta_y_el_comando(armar: Armar) -> None:
    http = await armar()
    tablero = (await http.get("/api/v1/tablero")).json()
    assert tablero["resumen"]["vacio"] is True and tablero["graficos"] is None
    assert tablero["comando_recalculo"] == "python tareas.py indicadores"
    assert tablero["periodos"] == [1, 7, 14, 30]


@pytest.mark.asyncio
async def prueba_el_tablero_con_datos_trae_resumen_y_graficos(armar: Armar) -> None:
    http = await armar(tablero=RepoTableroFalso(documentos_con_comparacion()))
    tablero = (await http.get("/api/v1/tablero", params={"dias": 1})).json()

    assert tablero["dias"] == 1 and tablero["resumen"]["vacio"] is False
    assert tablero["resumen"]["total"]["actual"] == 5
    assert {"estado", "severidad", "agente", "hora"} <= set(tablero["graficos"])
    assert (await http.get("/api/v1/tablero", params={"dias": 31})).status_code == 422


@pytest.mark.asyncio
async def prueba_el_tablero_tipado_publica_los_mismos_numeros_y_fechas_con_zona(armar: Armar) -> None:
    """T-66: los modelos no cambian ningun numero del repo; solo agregan la zona UTC a las fechas."""
    documentos = documentos_con_comparacion()
    # pymongo devuelve fechas sin zona (UTC): asi llegan del repo real.
    for documento in documentos:
        if "calculado_en" in documento:
            documento["calculado_en"] = documento["calculado_en"].replace(tzinfo=None)
    documentos[-1].update(
        calculado_en=datetime(2026, 11, 20, 3, 30, tzinfo=UTC).replace(tzinfo=None),
        monto_protegido_crc=1_250_000,
        decision_cantidad=2,
        decision_segundos_total=5400,
        pendientes=3,
        pendiente_mas_antigua=datetime(2026, 11, 19, 12, 0, tzinfo=UTC).replace(tzinfo=None),
    )
    http = await armar(tablero=RepoTableroFalso(documentos))
    cuerpo = (await http.get("/api/v1/tablero", params={"dias": 1})).json()
    esperado = resumir(documentos, 1)

    resumen = cuerpo["resumen"]
    assert resumen["total"] == esperado["total"]
    assert resumen["actual"]["por_estado"] == esperado["actual"]["por_estado"]
    assert resumen["kpi"]["monto_protegido_crc"]["actual"] == esperado["kpi"]["monto_protegido_crc"]["actual"]
    assert isinstance(resumen["kpi"]["monto_protegido_crc"]["actual"], int)
    assert resumen["kpi"]["tasa_confirmacion"] == esperado["kpi"]["tasa_confirmacion"]
    assert resumen["kpi"]["tiempo_decision_s"]["actual"] == 2700
    assert resumen["calculado_en"] == "2026-11-20T03:30:00Z"
    assert resumen["kpi"]["pendiente_mas_antigua"] == "2026-11-19T12:00:00Z"
    assert cuerpo["graficos"] == graficos_tablero(esperado)


# ---------------------------------------------------------------------------
# Sankey y grafo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_sankey_es_la_disposicion_del_servidor_con_bandas_enteras(
    armar: Armar,
) -> None:
    red = red_de_cuatro_saltos()
    http = await armar(red=RepoRedMulasFalso(red))
    respuesta = (await http.get("/api/v1/red-mulas/8712-4455/sankey")).json()

    esperado = disponer_sankey(red)
    assert esperado is not None
    sankey = respuesta["sankey"]
    assert sankey["columnas"] == esperado.columnas and sankey["saltos"] == 4
    assert [b["grosor"] for b in sankey["bandas"]] == [b["grosor"] for b in esperado.bandas]
    assert all(isinstance(b["grosor"], int) and isinstance(b["monto_crc"], int) for b in sankey["bandas"])
    assert sankey["bandas"][0]["d"] == esperado.bandas[0]["d"]
    assert respuesta["motivo"] is None


@pytest.mark.asyncio
async def prueba_el_sankey_desde_la_transaccion_pone_a_la_victima_primero(armar: Armar) -> None:
    http = await armar(red=RepoRedMulasFalso(red_de_cuatro_saltos()))
    sankey = (
        await http.get("/api/v1/red-mulas/8712-4455/sankey", params={"transaccion": "TXN-001"})
    ).json()["sankey"]

    victima = [n for n in sankey["nodos"] if n["victima"]]
    assert len(victima) == 1 and victima[0]["nombre"] == "Maria Rodriguez Vargas"
    assert victima[0]["columna"] == 0
    assert sankey["transferencias"] == 5


@pytest.mark.asyncio
async def prueba_sin_salidas_el_sankey_es_nulo_con_motivo_y_sin_cuenta_es_404(
    armar: Armar,
) -> None:
    sin_aristas = red_de_cuatro_saltos().model_copy(update={"aristas": []})
    http = await armar(red=RepoRedMulasFalso(sin_aristas))
    vacio = (await http.get("/api/v1/red-mulas/8712-4455/sankey")).json()
    assert vacio["sankey"] is None and "no tiene salidas" in vacio["motivo"]

    http = await armar(red=RepoRedMulasFalso(None))
    falta = await http.get("/api/v1/red-mulas/CTA-999/sankey")
    assert falta.status_code == 404 and falta.json()["error"]["codigo"] == "no_encontrado"


@pytest.mark.asyncio
async def prueba_el_grafo_trae_la_red_y_su_disposicion(armar: Armar) -> None:
    red = red_de_cuatro_saltos()
    http = await armar(red=RepoRedMulasFalso(red))
    respuesta = (await http.get("/api/v1/red-mulas/8712-4455/grafo")).json()

    assert respuesta["red"]["saltos_recorridos"] == 4
    assert respuesta["grafo"]["ancho"] == disponer_grafo(red)["ancho"]
    assert len(respuesta["grafo"]["nodos"]) == 5 and len(respuesta["grafo"]["aristas"]) == 4


ENTRADA_VIGENTE = {
    "_id": "RSK-001",
    "esquema_version": 1,
    "cuenta": "6033-9001",
    "tipo": "cuenta_mula",
    "motivo": "Reportada por otra entidad",
    "fecha_reporte": "2026-09-20T12:00:00+00:00",
    "vigente": True,
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("rol", "ve_entradas"), [("supervisor", True), ("agente", False)])
async def prueba_las_entradas_de_lista_solo_van_a_quien_puede_darlas_de_baja(
    armar: Armar, rol: str, ve_entradas: bool
) -> None:
    entrada = {**ENTRADA_VIGENTE, "fecha_reporte": datetime.fromisoformat(ENTRADA_VIGENTE["fecha_reporte"])}
    http = await armar(
        rol=rol,
        red=RepoRedMulasFalso(red_de_cuatro_saltos()),
        listas=RepoListasRiesgoFalso([entrada]),
    )
    respuesta = await http.get("/api/v1/red-mulas/8712-4455/grafo")
    assert respuesta.status_code == 200, respuesta.text
    entradas = respuesta.json()["entradas_lista"]
    assert [e["_id"] for e in entradas] == (["RSK-001"] if ve_entradas else [])


# ---------------------------------------------------------------------------
# Permisos de las lecturas: los mismos que su pagina
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_un_agente_no_lee_el_tablero_y_recibe_403_en_la_forma_del_error(
    armar: Armar,
) -> None:
    http = await armar(rol="agente")
    respuesta = await http.get("/api/v1/tablero")
    assert respuesta.status_code == 403
    assert respuesta.json()["error"]["codigo"] == "prohibido"
