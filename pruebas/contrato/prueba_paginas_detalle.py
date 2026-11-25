"""Pagina de detalle de alerta (H-18) con repositorios falsos. Sin Mongo."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
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
)
from app.main import crear_aplicacion
from app.repos.detalle import ProtocoloRepoDetalle
from pruebas.dobles.casos_demo import RepoCasosFalso
from pruebas.dobles.catalogo_reglas import (
    politica_como_documento,
    reglas_como_documentos,
)
from pruebas.dobles.repo_detalle_falso import RepoDetalleFalso, alerta_v1, alerta_v2
from pruebas.dobles.repos_falsos import RepoAlertasFalso, RepoReglasFalso
from pruebas.sesiones import encabezados_de

Armar = Callable[[dict[str, Any]], Awaitable[tuple[AsyncClient, RepoAlertasFalso]]]


@pytest_asyncio.fixture
async def armar() -> Any:
    abiertos: list[Any] = []

    async def _armar(alerta: dict[str, Any]) -> tuple[AsyncClient, RepoAlertasFalso]:
        alertas = RepoAlertasFalso([alerta])
        creada = crear_aplicacion("htmx")
        creada.dependency_overrides[dep_repo_alertas] = lambda: alertas
        creada.dependency_overrides[dep_repo_casos_destino] = lambda: RepoCasosFalso(alertas)
        creada.dependency_overrides[dep_repo_detalle] = lambda: RepoDetalleFalso(alertas.casos)
        creada.dependency_overrides[dep_repo_reglas] = lambda: RepoReglasFalso(
            reglas_como_documentos(), politica_como_documento()
        )
        gestor = LifespanManager(creada)
        gestionada = await gestor.__aenter__()
        http = AsyncClient(
            headers=encabezados_de(),
            transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
        )
        abiertos.append((gestor, http))
        return http, alertas

    yield _armar
    for gestor, http in abiertos:
        await http.aclose()
        await gestor.__aexit__(None, None, None)


def _botones(html: str) -> list[str]:
    return re.findall(r'data-destino="([a-z_]+)"', html)


def test_el_doble_cumple_el_protocolo() -> None:
    assert isinstance(RepoDetalleFalso(), ProtocoloRepoDetalle)


@pytest.mark.asyncio
async def prueba_v2_muestra_transaccion_perfil_reglas_y_la_suma(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    respuesta = await http.get("/detalle/ALR-001")
    assert respuesta.status_code == 200
    html = respuesta.text
    assert "₡750.000" in html and "8712-4455 (CTA-001)" in html and "6033-9001" in html
    assert "14/10/2026 22:47" in html  # hora de Costa Rica, no UTC
    assert "₡45.200" in html and "₡180.000" in html and "07:00 - 19:00" in html
    for fragmento in ("R-01", "REG-001", "Monto 750000 supera 10 veces el promedio 45200",
                      "+35", "+25", "+15", "POL-001", "versión 1"):
        assert fragmento in html
    # "no disponible: regla editada" se muestra, con mayuscula inicial.
    assert "No disponible: regla editada" in html
    assert re.search(r'id="suma-aportes">75<', html)
    assert 'id="puntaje-total">75' in html
    assert "aviso-v1" not in html
    # El corte alcanzado queda marcado: 75 cae en CRITICA (70-100).
    fila = re.search(r'<tr class="corte-alcanzado">\s*<td>(\w+)</td>', html)
    assert fila is not None and fila.group(1) == "Crítica"


@pytest.mark.asyncio
async def prueba_v1_muestra_los_ids_y_el_aviso(armar: Armar) -> None:
    http, _ = await armar(alerta_v1())
    html = (await http.get("/detalle/ALR-001")).text
    assert "alerta v1: migrar con python tareas.py migrar" in html
    assert all(regla in html for regla in ("REG-001", "REG-002", "REG-003"))
    assert "tabla-aportes" in html  # la tabla de cortes sigue
    assert 'id="tabla-aportes"' not in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("estado", "esperados"),
    [
        ("nueva", ["en_revision"]),
        ("en_revision", ["confirmada", "descartada", "escalada"]),
        ("confirmada", []),
        ("descartada", []),
        ("escalada", []),
    ],
)
async def prueba_solo_aparecen_los_botones_que_la_maquina_permite(
    armar: Armar, estado: str, esperados: list[str]
) -> None:
    http, _ = await armar(alerta_v2(estado, None if estado == "nueva" else "AGT-001"))
    html = (await http.get("/detalle/ALR-001")).text
    assert _botones(html) == esperados
    assert ("estado terminal" in html) == (not esperados)


@pytest.mark.asyncio
async def prueba_el_detalle_dice_quien_actua_y_ya_no_ofrece_elegir(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    html = (await http.get("/detalle/ALR-001")).text
    assert "Actúas como" in html and "AGT-001" in html
    assert "<select" not in html and 'name="agente_id"' not in html


@pytest.mark.asyncio
async def prueba_el_agente_del_historial_es_el_de_la_sesion(armar: Armar) -> None:
    """Un formulario que se arme a mano con otro agente_id no firma a nombre de otro."""
    http, alertas = await armar(alerta_v2())
    tomada = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=en_revision&agente_id=AGT-002&estado_esperado=nueva",
        headers=encabezados_de("agente", "AGT-001"),
    )
    assert tomada.status_code == 200
    historial = (await alertas.obtener("ALR-001"))["historial"]
    assert historial[-1]["agente_id"] == "AGT-001"


@pytest.mark.asyncio
async def prueba_alerta_inexistente_es_404(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    assert (await http.get("/detalle/ALR-099")).status_code == 404


@pytest.mark.asyncio
async def prueba_tomar_y_escalar_crea_el_caso_y_agrega_historial(armar: Armar) -> None:
    http, alertas = await armar(alerta_v2())
    tomada = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=en_revision&agente_id=AGT-001&nota=la+tomo&estado_esperado=nueva",
    )
    assert tomada.status_code == 200
    assert _botones(tomada.text) == ["confirmada", "descartada", "escalada"]
    assert 'hx-swap-oob="true"' in tomada.text and "la tomo" in tomada.text

    escalada = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=escalada&agente_id=AGT-001&estado_esperado=en_revision",
    )
    assert escalada.status_code == 200
    assert "CAS-001" in escalada.text and "caso-creado" in escalada.text
    assert "CAS-001" in alertas.casos
    # Recargar la pagina sigue mostrando el caso y el historial completo, en orden.
    html = (await http.get("/detalle/ALR-001")).text
    assert "CAS-001" in html
    acciones = re.findall(r"Acción: <strong>([^<]+)</strong>", html)
    assert acciones == ["Nueva", "En revisión", "Escalada"]


@pytest.mark.asyncio
async def prueba_el_409_de_otro_agente_se_ve_como_mensaje(armar: Armar) -> None:
    http, alertas = await armar(alerta_v2())
    alertas.interferir_antes_de_transicionar = True  # otro agente la toma en el medio
    respuesta = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=en_revision&agente_id=AGT-001&estado_esperado=nueva",
    )
    assert respuesta.status_code == 409
    assert "mensaje-error" in respuesta.text and "409" in respuesta.text
    assert alertas.escrituras == 0


@pytest.mark.asyncio
async def prueba_el_409_de_estado_desactualizado_y_de_transicion_invalida(armar: Armar) -> None:
    http, _ = await armar(alerta_v2("en_revision", "AGT-002"))
    desactualizado = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=en_revision&agente_id=AGT-001&estado_esperado=nueva",
    )
    assert desactualizado.status_code == 409 and "ya no esta en nueva" in desactualizado.text
    # Muestra la alerta como esta ahora: en revision, de AGT-002.
    assert _botones(desactualizado.text) == ["confirmada", "descartada", "escalada"]
    invalida = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=nueva&agente_id=AGT-002&estado_esperado=en_revision",
    )
    assert invalida.status_code == 409 and "transicion invalida" in invalida.text
    ajeno = await http.post(
        "/detalle/ALR-001/transicion",
        content="destino=confirmada&agente_id=AGT-001&estado_esperado=en_revision",
    )
    assert ajeno.status_code == 409 and "AGT-002" in ajeno.text


@pytest.mark.asyncio
async def prueba_datos_invalidos_son_422_con_mensaje(armar: Armar) -> None:
    http, alertas = await armar(alerta_v2())
    respuesta = await http.post("/detalle/ALR-001/transicion", content="destino=&agente_id=x")
    assert respuesta.status_code == 422 and "mensaje-error" in respuesta.text
    assert alertas.escrituras == 0


@pytest.mark.asyncio
async def prueba_el_panel_enlaza_cada_fila_al_detalle(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    html = (await http.get("/panel?vista=alertas")).text
    # El enlace lo pinta el servidor: la fila es abrible sin JavaScript.
    assert '<a href="/detalle/ALR-001">ALR-001</a>' in html
    assert 'role="row"' in html and 'role="columnheader"' in html and 'role="table"' in html
    assert 'aria-hidden="true"' not in html.split('id="cola"')[0].split("Cola de alertas")[-1]
    assert html.count('aria-live="polite"') >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("malo", ["abc", "ALR-1", "alr-001", "ALR-001x"])
async def prueba_un_id_mal_formado_es_404_amable_y_no_422(armar: Armar, malo: str) -> None:
    http, _ = await armar(alerta_v2())
    respuesta = await http.get(f"/detalle/{malo}")
    assert respuesta.status_code == 404
    assert "text/html" in respuesta.headers["content-type"]
    assert "Volver al panel" in respuesta.text and "No existe la alerta" in respuesta.text


@pytest.mark.asyncio
async def prueba_un_id_bien_formado_que_no_existe_tambien_es_404(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    respuesta = await http.get("/detalle/ALR-999")
    assert respuesta.status_code == 404 and "No existe la alerta ALR-999" in respuesta.text


@pytest.mark.asyncio
async def prueba_detalle_sin_id_redirige_al_panel(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    respuesta = await http.get("/detalle", follow_redirects=False)
    assert respuesta.status_code in (302, 307)
    assert respuesta.headers["location"] == "/panel"


@pytest.mark.asyncio
async def prueba_la_explicacion_no_trae_ruido_interno_y_si_la_evidencia(armar: Armar) -> None:
    alerta = alerta_v2()
    alerta["reglas_disparadas"][0]["explicacion"] = (
        "R-01 v1: monto muy alto (aporta 35; explicacion reconstruida por la migracion 001)"
    )
    http, _ = await armar(alerta)
    html = (await http.get("/detalle/ALR-001")).text
    assert "Monto muy alto" in html
    assert "R-01 v1:" not in html and "aporta 35" not in html and "migracion 001" not in html
    # Evidencia calculada con lo que la pagina ya tiene: 750000 / 45200 = 16,6.
    assert "Evidencia: Monto ₡750.000 contra un promedio de ₡45.200: 16,6x." in html
    assert "22:47 (hora CR) está fuera del horario habitual 07:00-19:00." in html


@pytest.mark.asyncio
async def prueba_los_cortes_son_los_de_la_politica_usada_o_se_rotulan(armar: Armar) -> None:
    http, _ = await armar(alerta_v2())
    html = (await http.get("/detalle/ALR-001")).text
    assert "son los de la política usada por la alerta" in html
    # Una politica que ya no esta en la historia: se dice, no se disimula.
    alerta = alerta_v2()
    alerta["politica"] = {"politica_id": "POL-001", "version": 9}
    http, _ = await armar(alerta)
    html = (await http.get("/detalle/ALR-001")).text
    assert "no están disponibles: se muestran los de la política vigente" in html


@pytest.mark.asyncio
async def prueba_el_formulario_tiene_ayuda_placeholder_y_confirmacion_en_terminales(
    armar: Armar,
) -> None:
    http, _ = await armar(alerta_v2("en_revision", "AGT-001"))
    html = (await http.get("/detalle/ALR-001")).text
    assert 'placeholder="' in html and "H-25" not in html
    assert html.count("hx-confirm=") == 3  # confirmar, descartar y escalar
    assert html.count('class="ayuda-boton"') == 3
    http, _ = await armar(alerta_v2())
    html = (await http.get("/detalle/ALR-001")).text
    assert "hx-confirm=" not in html  # tomar no es terminal
