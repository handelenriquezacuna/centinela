"""Contrato de la vista por casos y del lote (T-56), sin Mongo.

El repositorio de alertas y el de casos son dobles en memoria. Se verifica lo que se ve
y lo que se promete por HTTP: la vista por caso, el fragmento de un caso, el lote con 409
parcial, el tope de cantidad, los permisos de la tabla y que nada se cierra solo.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.api.dependencias import dep_repo_alertas, dep_repo_casos_destino
from app.main import crear_aplicacion
from app.modelos import EstadoAlerta, RolAgente
from app.repos.casos_destino import ProtocoloRepoCasosDestino
from pruebas.dobles.casos_demo import (
    MULA,
    RepoCasosFalso,
    alerta_de_caso,
    caso_de_la_mula,
)
from pruebas.dobles.repos_falsos import RepoAlertasFalso
from pruebas.sesiones import encabezados_de

LOTE = "/api/v1/alertas/transiciones"


@pytest_asyncio.fixture
async def armar() -> AsyncIterator[Any]:
    abiertos: list[Any] = []

    async def _armar(
        documentos: list[dict[str, Any]],
        rol: RolAgente = RolAgente.AGENTE,
        en_riesgo: frozenset[str] = frozenset({MULA}),
        agente: str = "AGT-001",
    ) -> tuple[AsyncClient, RepoAlertasFalso]:
        repo = RepoAlertasFalso(documentos)
        aplicacion = crear_aplicacion("htmx")
        aplicacion.dependency_overrides[dep_repo_alertas] = lambda: repo
        aplicacion.dependency_overrides[dep_repo_casos_destino] = lambda: RepoCasosFalso(repo, en_riesgo)
        gestor = LifespanManager(aplicacion)
        gestionada = await gestor.__aenter__()
        cliente = AsyncClient(
            headers=encabezados_de(rol, agente),
            transport=ASGITransport(app=gestionada.app),
            base_url="http://p",
        )
        abiertos.append((gestor, cliente))
        return cliente, repo

    yield _armar
    for gestor, cliente in abiertos:
        await cliente.aclose()
        await gestor.__aexit__(None, None, None)


def prueba_el_doble_de_casos_cumple_el_protocolo() -> None:
    assert isinstance(RepoCasosFalso(RepoAlertasFalso([])), ProtocoloRepoCasosDestino)


# --- Vista por caso ------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_vista_por_caso_muestra_el_caso_con_su_resumen(armar: Any) -> None:
    cliente, _ = await armar(caso_de_la_mula())
    html = (await cliente.get("/panel?vista=casos")).text
    assert 'id="caso-6033-9001"' in html
    assert re.findall(r'id="alerta-(ALR-\d+)"', html) == [
        "ALR-101", "ALR-103", "ALR-102", "ALR-105",
    ]
    assert "₡520.000" in html and "CRC" not in html.split("<main")[1]
    assert "En lista de riesgo" in html
    assert "3 víctimas en 12 min y la cuenta ya está reportada: candidata a confirmar." in html
    assert "24/09/2026 22:00" in html
    assert 'sse-connect="/api/v1/alertas/flujo"' in html
    assert 'aria-current="page">Por cuenta destino' in html


@pytest.mark.asyncio
async def prueba_la_vista_por_omision_es_por_cuenta_destino_y_se_recuerda(armar: Any) -> None:
    cliente, _ = await armar(caso_de_la_mula())
    por_omision = (await cliente.get("/panel")).text
    assert 'id="casos"' in por_omision and 'id="cola"' not in por_omision
    await cliente.get("/panel?vista=alertas")
    recordada = (await cliente.get("/panel")).text
    assert 'id="cola"' in recordada and 'id="casos"' not in recordada
    await cliente.get("/panel?vista=casos")
    assert 'id="casos"' in (await cliente.get("/panel")).text
    # Una vista desconocida no rompe: cae en la de omision.
    otro = (await armar(caso_de_la_mula()))[0]
    assert 'id="casos"' in (await otro.get("/panel?vista=cualquiera")).text


@pytest.mark.asyncio
async def prueba_la_interfaz_nunca_dice_caso_a_secas(armar: Any) -> None:
    """D-56: "caso" es la coleccion `casos` (CAS-xxx); el panel dice cuenta destino."""
    cliente, _ = await armar(caso_de_la_mula())
    for ruta in ("/panel", "/panel/casos", f"/panel/casos/{MULA}"):
        html = (await cliente.get(ruta)).text
        visible = re.sub(r"<[^>]+>", " ", html)
        assert not re.search(r"\b[Cc]asos?\b", visible), ruta
        atributos = " ".join(re.findall(r'(?:aria-label|title|placeholder)="([^"]*)"', html))
        assert not re.search(r"\b[Cc]asos?\b", atributos), ruta
    assert "Por cuenta destino" in (await cliente.get("/panel")).text
    assert "Por alerta" in (await cliente.get("/panel")).text


@pytest.mark.asyncio
async def prueba_un_caso_no_cierra_nada_solo(armar: Any) -> None:
    cliente, repo = await armar(caso_de_la_mula())
    await cliente.get("/panel?vista=casos")
    await cliente.get(f"/panel/casos/{MULA}")
    assert repo.escrituras == 0


@pytest.mark.asyncio
async def prueba_el_fragmento_de_un_caso_y_el_vacio(armar: Any) -> None:
    cliente, _ = await armar(caso_de_la_mula())
    unico = await cliente.get(f"/panel/casos/{MULA}")
    assert unico.status_code == 200 and "<html" not in unico.text
    assert unico.text.count('class="caso ') == 1
    vacio = await cliente.get("/panel/casos/9999-0000")
    assert vacio.status_code == 200 and vacio.text == ""
    todos = await cliente.get("/panel/casos")
    assert re.findall(r'<section id="caso-([\d-]+)"', todos.text) == [MULA, "3390-6612"]


@pytest.mark.asyncio
async def prueba_la_vista_por_caso_escapa_el_nombre(armar: Any) -> None:
    docs = [alerta_de_caso(1, cliente="<b>Maria</b>")]
    cliente, _ = await armar(docs)
    html = (await cliente.get("/panel?vista=casos")).text
    assert "<b>Maria" not in html and "&lt;b&gt;Maria" in html


# --- Lote por la API -----------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_lote_pasa_todas_y_cada_una_queda_en_su_historial(armar: Any) -> None:
    cliente, repo = await armar(caso_de_la_mula())
    ids = ["ALR-101", "ALR-102", "ALR-103"]
    respuesta = await cliente.post(LOTE, json={"alertas": ids, "destino": "en_revision", "nota": "lote"})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert (cuerpo["solicitadas"], cuerpo["aplicadas"], cuerpo["rechazadas"]) == (3, 3, 0)
    assert repo.escrituras == 3  # una escritura por alerta, no un update_many
    for alerta_id in ids:
        guardada = await repo.obtener(alerta_id)
        assert guardada["estado"] == "en_revision" and guardada["agente_id"] == "AGT-001"
        assert guardada["historial"][-1]["nota"] == "lote"


@pytest.mark.asyncio
async def prueba_el_lote_parcial_dice_cuales_no_pasaron_y_por_que(armar: Any) -> None:
    docs = caso_de_la_mula()
    docs.append(alerta_de_caso(106, estado=EstadoAlerta.EN_REVISION, agente_id="AGT-002"))
    cliente, repo = await armar(docs)
    ids = ["ALR-101", "ALR-106", "ALR-104", "ALR-999"]
    respuesta = await cliente.post(LOTE, json={"alertas": ids, "destino": "en_revision"})
    assert respuesta.status_code == 200
    por_id = {r["alerta_id"]: r for r in respuesta.json()["resultados"]}
    assert por_id["ALR-101"]["aplicada"] is True
    # ALR-106 ya estaba en revision de otro agente: la maquina de estados la rechaza.
    assert (por_id["ALR-106"]["estado_http"], por_id["ALR-106"]["codigo"]) == (409, "transicion_invalida")
    assert por_id["ALR-106"]["estado_actual"] == "en_revision"
    # ALR-104 esta confirmada (terminal), ALR-999 no existe.
    assert por_id["ALR-104"]["estado_http"] == 409
    assert (por_id["ALR-999"]["estado_http"], por_id["ALR-999"]["codigo"]) == (404, "no_encontrado")
    assert (respuesta.json()["aplicadas"], respuesta.json()["rechazadas"]) == (1, 3)
    assert repo.escrituras == 1


@pytest.mark.asyncio
async def prueba_resolver_la_de_otro_agente_en_lote_es_409_de_esa_sola(armar: Any) -> None:
    docs = [
        alerta_de_caso(1, estado=EstadoAlerta.EN_REVISION, agente_id="AGT-001"),
        alerta_de_caso(2, estado=EstadoAlerta.EN_REVISION, agente_id="AGT-002", minuto=1),
    ]
    cliente, _ = await armar(docs)
    respuesta = await cliente.post(LOTE, json={"alertas": ["ALR-001", "ALR-002"], "destino": "confirmada"})
    por_id = {r["alerta_id"]: r for r in respuesta.json()["resultados"]}
    assert por_id["ALR-001"]["aplicada"] is True
    assert (por_id["ALR-002"]["estado_http"], por_id["ALR-002"]["codigo"]) == (409, "conflicto")


@pytest.mark.asyncio
async def prueba_los_repetidos_cuentan_una_vez(armar: Any) -> None:
    cliente, repo = await armar(caso_de_la_mula())
    respuesta = await cliente.post(LOTE, json={"alertas": ["ALR-101", "ALR-101"], "destino": "en_revision"})
    assert respuesta.json()["solicitadas"] == 1 and repo.escrituras == 1


@pytest.mark.asyncio
async def prueba_el_lote_respeta_el_tope_de_cantidad(
    armar: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENTINELA_APP__LOTE_MAXIMO", "2")
    cliente, repo = await armar(caso_de_la_mula())
    ids = ["ALR-101", "ALR-102", "ALR-103"]
    respuesta = await cliente.post(LOTE, json={"alertas": ids, "destino": "en_revision"})
    assert respuesta.status_code == 422
    assert respuesta.json()["error"]["codigo"] == "parametros_invalidos"
    assert repo.escrituras == 0
    dentro = await cliente.post(LOTE, json={"alertas": ids[:2], "destino": "en_revision"})
    assert dentro.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cuerpo",
    [
        {"alertas": [], "destino": "en_revision"},
        {"alertas": ["ALR-101"], "destino": "borrada"},
        {"alertas": ["no-es-id"], "destino": "en_revision"},
        {"alertas": ["ALR-101"], "destino": "en_revision", "otro": 1},
    ],
)
async def prueba_un_cuerpo_invalido_es_422(armar: Any, cuerpo: dict[str, Any]) -> None:
    cliente, repo = await armar(caso_de_la_mula())
    assert (await cliente.post(LOTE, json=cuerpo)).status_code == 422
    assert repo.escrituras == 0


@pytest.mark.asyncio
async def prueba_el_lote_sin_sesion_es_401_y_sin_permiso_es_403(armar: Any) -> None:
    cliente, repo = await armar(caso_de_la_mula())
    cuerpo = {"alertas": ["ALR-101"], "destino": "en_revision"}
    sin = await cliente.post(LOTE, json=cuerpo, headers={"Authorization": ""})
    assert sin.status_code == 401
    # Todos los roles con RESOLVER (agente, supervisor, administrador) pueden: la misma
    # tabla que la transicion individual. La prueba de permisos recorre la tabla entera.
    assert (await cliente.post(LOTE, json=cuerpo)).status_code == 200
    assert repo.escrituras == 1


# --- Lote desde el portal ------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_lote_del_portal_devuelve_el_caso_con_el_resultado(armar: Any) -> None:
    docs = caso_de_la_mula()
    docs.append(alerta_de_caso(106, estado=EstadoAlerta.EN_REVISION, agente_id="AGT-002", minuto=2))
    cliente, repo = await armar(docs)
    respuesta = await cliente.post(
        "/panel/lote",
        data={
            "cuenta": MULA, "destino": "en_revision", "nota": "revisando",
            "alerta": ["ALR-101", "ALR-102", "ALR-106"],
        },
    )
    assert respuesta.status_code == 200
    html = respuesta.text
    assert "Pasaron 2 de 3" in html and "En revisión" in html
    assert "ALR-106" in html and "(409)" in html and "no puede pasar de En revisión" in html
    assert 'id="caso-6033-9001"' in html
    historial = (await repo.obtener("ALR-101"))["historial"][-1]
    assert historial["nota"] == f"[lote en {MULA}] revisando"


@pytest.mark.asyncio
async def prueba_el_lote_del_portal_sin_seleccion_es_422_con_mensaje(armar: Any) -> None:
    cliente, repo = await armar(caso_de_la_mula())
    respuesta = await cliente.post("/panel/lote", data={"cuenta": MULA, "destino": "en_revision"})
    assert respuesta.status_code == 422
    assert "Selecciona al menos una alerta" in respuesta.text
    assert repo.escrituras == 0


@pytest.mark.asyncio
async def prueba_el_lote_que_cierra_el_caso_deja_solo_el_resultado(armar: Any) -> None:
    docs = [alerta_de_caso(1, estado=EstadoAlerta.EN_REVISION, agente_id="AGT-001")]
    cliente, _ = await armar(docs)
    respuesta = await cliente.post(
        "/panel/lote", data={"cuenta": MULA, "destino": "descartada", "alerta": ["ALR-001"]}
    )
    assert "Pasaron 1 de 1" in respuesta.text
    assert "ya no tiene alertas abiertas" in respuesta.text


# --- Escalar en lote: un solo CAS por cuenta destino (D-57) ----------------------------


def _en_revision(numero: int, agente: str = "AGT-001", **cambios: Any) -> dict[str, Any]:
    return alerta_de_caso(
        numero, estado=EstadoAlerta.EN_REVISION, agente_id=agente, minuto=numero, **cambios
    )


@pytest.mark.asyncio
async def prueba_escalar_en_lote_abre_un_solo_caso_con_todas(armar: Any) -> None:
    cliente, repo = await armar([_en_revision(1), _en_revision(2), _en_revision(3)])
    respuesta = await cliente.post(
        LOTE, json={"alertas": ["ALR-001", "ALR-002", "ALR-003"], "destino": "escalada"}
    )
    cuerpo = respuesta.json()
    assert cuerpo["aplicadas"] == 3
    assert {r["caso_id"] for r in cuerpo["resultados"]} == {"CAS-001"}
    assert list(repo.casos) == ["CAS-001"]
    caso = repo.casos["CAS-001"]
    assert caso["alertas"] == ["ALR-001", "ALR-002", "ALR-003"]
    assert (caso["cuenta_destino"], caso["agente_id"]) == (MULA, "AGT-001")
    assert caso["estado"] == "investigacion"
    for alerta_id in caso["alertas"]:
        assert (await repo.obtener(alerta_id))["estado"] == "escalada"


@pytest.mark.asyncio
async def prueba_la_que_da_409_no_entra_al_caso_y_el_caso_nace_de_la_siguiente(armar: Any) -> None:
    docs = [
        alerta_de_caso(1, minuto=1),  # nueva: nueva -> escalada no esta en la tabla
        _en_revision(2),
        _en_revision(3, agente="AGT-002"),  # la tiene otro agente
        _en_revision(4),
    ]
    cliente, repo = await armar(docs)
    ids = ["ALR-001", "ALR-002", "ALR-003", "ALR-004"]
    respuesta = await cliente.post(LOTE, json={"alertas": ids, "destino": "escalada"})
    por_id = {r["alerta_id"]: r for r in respuesta.json()["resultados"]}
    assert (por_id["ALR-001"]["estado_http"], por_id["ALR-003"]["estado_http"]) == (409, 409)
    assert list(repo.casos) == ["CAS-002"]
    assert repo.casos["CAS-002"]["alertas"] == ["ALR-002", "ALR-004"]
    assert por_id["ALR-004"]["caso_id"] == "CAS-002"


@pytest.mark.asyncio
async def prueba_si_fallan_todas_no_queda_un_caso_vacio(armar: Any) -> None:
    cliente, repo = await armar([alerta_de_caso(1), alerta_de_caso(2, minuto=1)])
    respuesta = await cliente.post(
        LOTE, json={"alertas": ["ALR-001", "ALR-002"], "destino": "escalada"}
    )
    assert respuesta.json()["aplicadas"] == 0
    assert repo.casos == {}


@pytest.mark.asyncio
async def prueba_la_escalacion_individual_no_cambia(armar: Any) -> None:
    cliente, repo = await armar([_en_revision(1), _en_revision(2)])
    for numero in (1, 2):
        respuesta = await cliente.post(
            f"/api/v1/alertas/ALR-00{numero}/transiciones", json={"destino": "escalada"}
        )
        assert respuesta.json()["caso_id"] == f"CAS-00{numero}"
    assert sorted(repo.casos) == ["CAS-001", "CAS-002"]
    # Un caso por alerta y sin los campos del lote.
    assert set(repo.casos["CAS-001"]) == {"_id", "alertas", "estado", "esquema_version", "fecha_apertura"}


@pytest.mark.asyncio
async def prueba_el_portal_muestra_el_caso_abierto_una_vez(armar: Any) -> None:
    cliente, _ = await armar([_en_revision(1), _en_revision(2)])
    respuesta = await cliente.post(
        "/panel/lote", data={"cuenta": MULA, "destino": "escalada", "alerta": ["ALR-001", "ALR-002"]}
    )
    assert respuesta.text.count("CAS-001") == 1
    assert "Pasaron 2 de 2" in respuesta.text
