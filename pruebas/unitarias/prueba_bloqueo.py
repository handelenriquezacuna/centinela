"""T-83: el contador de intentos, la ventana por IP y el flujo de `autenticar_con_limites`."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.seguridad import autenticacion, contrasenas
from app.seguridad.autenticacion import autenticar_con_limites
from app.seguridad.bloqueo import (
    LimitadorIp,
    bloqueo_vencido,
    calcular_fallo,
    esta_bloqueada,
)
from pruebas.dobles.repo_agentes_falso import CONTRASENA, RepoAgentesFalso

AHORA = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
IP = "10.0.0.7"


# ---------------------------------------------------------------------------
# calcular_fallo
# ---------------------------------------------------------------------------


def prueba_los_fallos_se_suman_y_el_quinto_bloquea_quince_minutos() -> None:
    agente: dict = {"_id": "AGT-001"}
    for esperado in (1, 2, 3, 4):
        resultado = calcular_fallo(agente, maximo=5, bloqueo_minutos=15, ahora=AHORA)
        assert (resultado.intentos_fallidos, resultado.bloqueado_hasta) == (esperado, None)
        agente["intentos_fallidos"] = resultado.intentos_fallidos

    quinto = calcular_fallo(agente, maximo=5, bloqueo_minutos=15, ahora=AHORA)

    assert quinto.intentos_fallidos == 5 and quinto.bloqueo_nuevo
    assert quinto.bloqueado_hasta == AHORA + timedelta(minutes=15)


def prueba_con_el_bloqueo_vigente_intentar_mas_no_cambia_nada() -> None:
    hasta = AHORA + timedelta(minutes=10)
    agente = {"intentos_fallidos": 5, "bloqueado_hasta": hasta}

    resultado = calcular_fallo(agente, maximo=5, bloqueo_minutos=15, ahora=AHORA)

    assert resultado.intentos_fallidos == 5
    assert resultado.bloqueado_hasta == hasta and not resultado.bloqueo_nuevo


def prueba_con_el_bloqueo_vencido_el_conteo_empieza_de_nuevo() -> None:
    agente = {"intentos_fallidos": 5, "bloqueado_hasta": AHORA - timedelta(seconds=1)}

    assert bloqueo_vencido(agente, AHORA) and not esta_bloqueada(agente, AHORA)
    resultado = calcular_fallo(agente, maximo=5, bloqueo_minutos=15, ahora=AHORA)

    assert resultado.intentos_fallidos == 1 and resultado.bloqueado_hasta is None


def prueba_el_bloqueo_justo_en_su_instante_ya_no_vale() -> None:
    assert not esta_bloqueada({"bloqueado_hasta": AHORA}, AHORA)
    assert esta_bloqueada({"bloqueado_hasta": AHORA + timedelta(milliseconds=1)}, AHORA)


def prueba_un_maximo_de_uno_bloquea_al_primer_fallo() -> None:
    assert calcular_fallo({}, maximo=1, bloqueo_minutos=1, ahora=AHORA).bloqueo_nuevo


def prueba_una_fecha_ingenua_de_la_base_se_trata_como_utc() -> None:
    ingenua = (AHORA + timedelta(minutes=5)).replace(tzinfo=None)
    assert esta_bloqueada({"bloqueado_hasta": ingenua}, AHORA)


# ---------------------------------------------------------------------------
# LimitadorIp
# ---------------------------------------------------------------------------


def prueba_la_ip_se_frena_al_llegar_al_limite_y_se_libera_al_salir_de_la_ventana() -> None:
    limitador = LimitadorIp(maximo=3, ventana_segundos=60)
    for segundo in range(3):
        assert not limitador.bloqueada(IP, AHORA + timedelta(seconds=segundo))
        limitador.registrar_fallo(IP, AHORA + timedelta(seconds=segundo))

    assert limitador.bloqueada(IP, AHORA + timedelta(seconds=3))
    assert not limitador.bloqueada("10.0.0.8", AHORA + timedelta(seconds=3))
    # El primer fallo (t=0) sale de la ventana en t=60: quedan 2 y la IP se libera.
    assert not limitador.bloqueada(IP, AHORA + timedelta(seconds=60))
    assert len(limitador) == 1


def prueba_una_ip_sin_fallos_no_deja_rastro() -> None:
    limitador = LimitadorIp(maximo=2, ventana_segundos=10)
    limitador.registrar_fallo(IP, AHORA)

    assert not limitador.bloqueada(IP, AHORA + timedelta(seconds=11))
    assert len(limitador) == 0


def prueba_la_memoria_del_limitador_esta_acotada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.seguridad.bloqueo.MAXIMO_IPS_RECORDADAS", 5)
    limitador = LimitadorIp(maximo=3, ventana_segundos=600)

    for numero in range(50):
        limitador.registrar_fallo(f"10.0.1.{numero}", AHORA)

    assert len(limitador) <= 5


# ---------------------------------------------------------------------------
# autenticar_con_limites
# ---------------------------------------------------------------------------


async def _intentar(
    repo: RepoAgentesFalso,
    limitador: LimitadorIp,
    agente_id: str = "AGT-001",
    contrasena: str = "mala",
    ahora: datetime = AHORA,
    ip: str = IP,
) -> dict | None:
    return await autenticar_con_limites(
        repo, limitador, ip, agente_id, contrasena, maximo=5, bloqueo_minutos=15, ahora=ahora
    )


@pytest.mark.asyncio
async def prueba_cinco_fallos_bloquean_y_la_clave_buena_ya_no_entra() -> None:
    repo, limitador = RepoAgentesFalso(), LimitadorIp(100, 600)
    for _ in range(5):
        assert await _intentar(repo, limitador) is None

    assert esta_bloqueada(repo.agentes["AGT-001"], AHORA)
    assert await _intentar(repo, limitador, contrasena=CONTRASENA) is None
    # Intentar durante el bloqueo no lo alarga.
    assert repo.agentes["AGT-001"]["bloqueado_hasta"] == AHORA + timedelta(minutes=15)


@pytest.mark.asyncio
async def prueba_pasado_el_plazo_la_clave_buena_entra_y_limpia_la_cuenta() -> None:
    repo, limitador = RepoAgentesFalso(), LimitadorIp(100, 600)
    for _ in range(5):
        await _intentar(repo, limitador)

    despues = AHORA + timedelta(minutes=15, seconds=1)
    agente = await _intentar(repo, limitador, contrasena=CONTRASENA, ahora=despues)

    assert agente is not None
    assert "intentos_fallidos" not in repo.agentes["AGT-001"]
    assert "bloqueado_hasta" not in repo.agentes["AGT-001"]


@pytest.mark.asyncio
async def prueba_un_acierto_reinicia_el_contador() -> None:
    repo, limitador = RepoAgentesFalso(), LimitadorIp(100, 600)
    for _ in range(4):
        await _intentar(repo, limitador)
    assert repo.agentes["AGT-001"]["intentos_fallidos"] == 4

    assert await _intentar(repo, limitador, contrasena=CONTRASENA) is not None
    assert "intentos_fallidos" not in repo.agentes["AGT-001"]

    await _intentar(repo, limitador)
    assert repo.agentes["AGT-001"]["intentos_fallidos"] == 1


@pytest.mark.asyncio
async def prueba_una_cuenta_inexistente_cuenta_para_la_ip_y_nada_mas() -> None:
    repo, limitador = RepoAgentesFalso(), LimitadorIp(3, 600)
    for numero in range(3):
        assert await _intentar(repo, limitador, agente_id=f"AGT-9{numero}9") is None

    assert limitador.bloqueada(IP, AHORA)
    # La IP frenada tampoco entra con una cuenta y clave buenas.
    assert await _intentar(repo, limitador, contrasena=CONTRASENA) is None
    assert "intentos_fallidos" not in repo.agentes["AGT-001"]


@pytest.mark.asyncio
async def prueba_otra_ip_no_sufre_el_bloqueo_de_la_ip_frenada() -> None:
    repo, limitador = RepoAgentesFalso(), LimitadorIp(2, 600)
    for _ in range(2):
        await _intentar(repo, limitador, agente_id="AGT-099")

    assert await _intentar(repo, limitador, contrasena=CONTRASENA, ip="10.9.9.9") is not None


@pytest.mark.asyncio
async def prueba_los_aciertos_no_gastan_el_presupuesto_de_la_ip() -> None:
    repo, limitador = RepoAgentesFalso(), LimitadorIp(2, 600)
    for _ in range(10):
        assert await _intentar(repo, limitador, contrasena=CONTRASENA) is not None

    assert not limitador.bloqueada(IP, AHORA)


@pytest.mark.asyncio
async def prueba_un_agente_inactivo_con_la_clave_buena_cuenta_como_fallo() -> None:
    repo, limitador = RepoAgentesFalso(), LimitadorIp(100, 600)

    assert await _intentar(repo, limitador, agente_id="AGT-004", contrasena=CONTRASENA) is None
    assert repo.agentes["AGT-004"]["intentos_fallidos"] == 1


@pytest.mark.asyncio
async def prueba_el_hash_se_calcula_siempre_aunque_la_cuenta_no_exista_o_este_bloqueada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llamadas: list[bool] = []
    original = contrasenas.verificar

    def espia(contrasena: str, credencial: dict | None) -> bool:
        llamadas.append(credencial is not None)
        return original(contrasena, credencial)

    monkeypatch.setattr(autenticacion.contrasenas, "verificar", espia)
    repo, limitador = RepoAgentesFalso(), LimitadorIp(100, 600)
    for _ in range(5):
        await _intentar(repo, limitador)
    llamadas.clear()

    await _intentar(repo, limitador)  # bloqueada
    await _intentar(repo, limitador, agente_id="AGT-099")  # no existe

    assert len(llamadas) == 2


@pytest.mark.asyncio
async def prueba_la_auditoria_registra_el_fallo_el_bloqueo_y_el_desbloqueo(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo, limitador = RepoAgentesFalso(), LimitadorIp(100, 600)
    with caplog.at_level(logging.INFO, logger="centinela.auditoria"):
        for _ in range(5):
            await _intentar(repo, limitador)
        await _intentar(repo, limitador, contrasena=CONTRASENA)
        await _intentar(
            repo, limitador, contrasena=CONTRASENA, ahora=AHORA + timedelta(minutes=16)
        )

    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert texto.count("login_fallido") == 5
    assert sum(1 for linea in texto.splitlines() if linea.startswith("bloqueo agente=AGT-001")) == 1
    assert "login_en_cuenta_bloqueada" in texto
    assert "desbloqueo agente=AGT-001 motivo=vencio_el_plazo" in texto


@pytest.mark.asyncio
async def prueba_el_identificador_tecleado_no_inyecta_lineas_en_el_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo, limitador = RepoAgentesFalso(), LimitadorIp(100, 600)
    with caplog.at_level(logging.INFO, logger="centinela.auditoria"):
        await _intentar(repo, limitador, agente_id="AGT-1\nbloqueo agente=AGT-001 falso")

    assert all("\n" not in r.getMessage() for r in caplog.records)
