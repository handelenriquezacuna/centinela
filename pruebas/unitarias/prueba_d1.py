"""H-09 · D1 probado sin base de datos: el orden de las escrituras y los reprocesos.

Lo que estas pruebas sostienen, cada una con el doble `RepoDeteccionFalso`:

- la alerta se escribe ANTES que el token, y el token solo cuando el evento termino;
- un reproceso (la transaccion ya tenia alerta) no revienta ni duplica: avanza;
- un fallo se reintenta con espera progresiva y, agotado, va al descarte;
- se reanuda con el token guardado, y un token inservible resincroniza desde ahora;
- D1 late por vuelta, con o sin evento.

Contra Mongo de verdad, el mismo contrato lo prueba `pruebas/integracion/prueba_d1_mongo.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.deteccion.d1 import NOMBRE_D1, alerta_id_para, d1, espera_progresiva
from app.modelos import EstadoAlerta, Severidad
from app.nucleo.config import AjustesDeteccion
from app.repos.deteccion import EventoTransaccion, ProtocoloRepoDeteccion, RezagoMedido
from app.supervision import InformeTarea, Supervisor
from pruebas.dobles.datos_demo import TRANSACCIONES
from pruebas.dobles.repo_deteccion_falso import RepoDeteccionFalso, token

# `lote_maximo=1`: estas pruebas sostienen el contrato por evento (una alerta, un token). El
# lote tiene las suyas en `prueba_d1_lotes.py`.
AJUSTES = AjustesDeteccion(
    lote_maximo=1,
    espera_cursor_ms=10,
    reintentos=2,
    espera_inicial_segundos=0.001,
    espera_maxima_segundos=0.004,
)
AHORA = datetime(2026, 9, 25, 4, 47, 5, tzinfo=UTC)


def _documento(transaccion_id: str) -> dict[str, Any]:
    transaccion = next(t for t in TRANSACCIONES if t.transaccion_id == transaccion_id)
    return transaccion.a_documento()


def _evento(numero: int, transaccion_id: str | None) -> EventoTransaccion:
    documento = _documento(transaccion_id) if transaccion_id is not None else None
    return EventoTransaccion(token=token(numero), documento=documento)


async def _correr(repo: RepoDeteccionFalso) -> InformeTarea:
    """Corre D1 hasta que el doble entrega todo, y lo apaga como el lifespan."""
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(repo, supervisor, ajustes=AJUSTES, reloj=lambda: AHORA))
    try:
        await repo.esperar_procesado(supervisor)
        return supervisor.informe().tarea(NOMBRE_D1)
    finally:
        await supervisor.detener()


def prueba_el_doble_cumple_el_protocolo() -> None:
    assert isinstance(RepoDeteccionFalso([]), ProtocoloRepoDeteccion)


# ---------------------------------------------------------------------------
# Orden de escrituras
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_alerta_se_escribe_antes_que_el_token() -> None:
    repo = RepoDeteccionFalso([_evento(1, "TXN-001"), _evento(2, "TXN-002")])

    await _correr(repo)

    # TXN-002 no supera el umbral: no hay alerta, pero su token igual avanza.
    assert repo.bitacora == ["alerta:TXN-001", "token:TXN-001", "token:TXN-002"]
    assert repo.token_guardado == token(2)


@pytest.mark.asyncio
async def prueba_la_alerta_es_la_del_caso_documentado() -> None:
    repo = RepoDeteccionFalso([_evento(1, "TXN-001")])

    await _correr(repo)

    alerta = repo.alertas["TXN-001"]
    assert alerta.alerta_id == "ALR-001"
    assert alerta.puntaje == 75
    assert alerta.severidad == Severidad.CRITICA
    # H-26: v2, con la copia embebida que ya producia el evaluador y la politica.
    assert alerta.esquema_version == 2
    assert alerta.regla_ids == ["REG-001", "REG-002", "REG-003"]
    assert [(r.codigo, r.version, r.peso) for r in alerta.reglas_aplicadas] == [
        ("R-01", 1, 35),
        ("R-03", 1, 25),
        ("R-05", 1, 15),
    ]
    assert all(r.explicacion for r in alerta.reglas_aplicadas)
    assert (alerta.politica.politica_id, alerta.politica.version) == ("POL-001", 1)
    assert alerta.estado == EstadoAlerta.NUEVA
    assert alerta.fecha_creacion == AHORA
    assert (alerta.cuenta_origen, alerta.cuenta_destino) == ("8712-4455", "6033-9001")
    assert alerta.monto_crc == 280_000
    assert alerta.cliente_nombre == "Maria Rodriguez Vargas"
    creacion = alerta.historial[0]
    assert creacion.accion == EstadoAlerta.NUEVA
    assert creacion.agente_id is None
    assert "POL-001 v1" in creacion.nota


@pytest.mark.asyncio
async def prueba_un_reproceso_no_duplica_y_avanza_el_token() -> None:
    """El proceso murio entre la alerta y el token: al reanudar, el evento vuelve.

    El indice unico rechaza la segunda alerta; D1 lo cuenta como ya procesada, guarda
    el token y sigue. Sin error en `/estado`: es el funcionamiento normal.
    """
    repo = RepoDeteccionFalso([_evento(1, "TXN-001")], alertas_existentes=["TXN-001"])

    fila = await _correr(repo)

    assert repo.bitacora == ["duplicada:TXN-001", "token:TXN-001"]
    assert repo.alertas == {}
    assert fila.ultimo_error is None


# ---------------------------------------------------------------------------
# Reintentos y descarte
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_un_fallo_pasajero_se_reintenta() -> None:
    repo = RepoDeteccionFalso([_evento(1, "TXN-001")], fallos_al_insertar=1)

    await _correr(repo)

    assert repo.bitacora == ["fallo_insertar:TXN-001", "alerta:TXN-001", "token:TXN-001"]
    assert repo.descartes == []


@pytest.mark.asyncio
async def prueba_agotados_los_reintentos_va_al_descarte_y_sigue() -> None:
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001"), _evento(2, "TXN-002")], fallos_al_insertar=99
    )

    fila = await _correr(repo)

    # 1 intento + 2 reintentos; despues el descarte, y recien ahi el token.
    assert repo.bitacora == [
        "fallo_insertar:TXN-001",
        "fallo_insertar:TXN-001",
        "fallo_insertar:TXN-001",
        "descarte:TXN-001",
        "token:TXN-001",
        "token:TXN-002",
    ]
    assert repo.descartes[0]["intentos"] == 3
    assert "ConnectionError" in repo.descartes[0]["motivo"]
    assert fila.ultimo_error is not None


@pytest.mark.asyncio
async def prueba_una_transaccion_malformada_va_directo_al_descarte() -> None:
    """Un error de validacion no se arregla reintentando."""
    malformada = _documento("TXN-001") | {"_id": "TXN-009", "monto_crc": 750_000.0}
    repo = RepoDeteccionFalso([EventoTransaccion(token=token(1), documento=malformada)])

    await _correr(repo)

    assert repo.bitacora == ["descarte:TXN-009", "token:TXN-009"]
    assert repo.descartes[0]["intentos"] == 1


def prueba_la_espera_se_duplica_hasta_el_tope() -> None:
    ajustes = AjustesDeteccion(espera_inicial_segundos=0.5, espera_maxima_segundos=3)

    assert [espera_progresiva(i, ajustes) for i in range(5)] == [0.5, 1.0, 2.0, 3, 3]


# ---------------------------------------------------------------------------
# Reanudacion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_reanuda_desde_el_token_guardado() -> None:
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001"), _evento(2, "TXN-002")], token_guardado=token(1)
    )

    await _correr(repo)

    assert repo.aperturas == [token(1)]
    assert repo.bitacora == ["token:TXN-002"], "TXN-001 ya estaba procesada"


@pytest.mark.asyncio
async def prueba_un_token_inservible_resincroniza_desde_ahora() -> None:
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001")], token_guardado=token(99), rechazar_token=True
    )

    fila = await _correr(repo)

    assert repo.aperturas == [token(99), None]
    assert repo.bitacora == ["alerta:TXN-001", "token:TXN-001"]
    assert fila.ultimo_error is not None
    assert "ChangeStreamHistoryLost" in fila.ultimo_error.mensaje


@pytest.mark.asyncio
async def prueba_sin_token_guarda_el_de_la_primera_vuelta_vacia() -> None:
    """Una caida antes de la primera transaccion no puede reiniciar desde ahora."""
    repo = RepoDeteccionFalso(
        [
            EventoTransaccion(token=token(1), documento=None),
            EventoTransaccion(token=token(2), documento=None),
        ]
    )

    fila = await _correr(repo)

    assert repo.bitacora == ["token:vacio"]
    assert repo.token_guardado == token(1)
    assert fila.eventos_procesados == 2, "cada vuelta vacia es un latido"


@pytest.mark.asyncio
async def prueba_un_flujo_que_se_cae_se_reabre_desde_el_ultimo_token() -> None:
    class FlujoQueSeCae(RepoDeteccionFalso):
        def __init__(self) -> None:
            super().__init__([_evento(1, "TXN-001"), _evento(2, "TXN-002")])
            self.caidas = 1

        async def observar_transacciones(
            self, *, desde_token: Mapping[str, Any] | None, espera_ms: int
        ) -> AsyncIterator[EventoTransaccion]:
            async for vuelta in super().observar_transacciones(
                desde_token=desde_token, espera_ms=espera_ms
            ):
                yield vuelta
                if self.caidas and vuelta.token == token(1):
                    self.caidas -= 1
                    raise ConnectionError("se cayo el primario")

    repo = FlujoQueSeCae()

    fila = await _correr(repo)

    assert repo.aperturas == [None, token(1)]
    assert repo.bitacora == ["alerta:TXN-001", "token:TXN-001", "token:TXN-002"]
    assert fila.ultimo_error is not None


# ---------------------------------------------------------------------------
# Identificador
# ---------------------------------------------------------------------------


def prueba_el_id_de_la_alerta_sale_de_la_transaccion() -> None:
    assert alerta_id_para("TXN-001") == "ALR-001"
    assert alerta_id_para("TXN-1234") == "ALR-1234"
    with pytest.raises(ValueError):
        alerta_id_para("ALR-001")


# ---------------------------------------------------------------------------
# T-18: sin reglas activas, /estado degradado
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_sin_reglas_activas_d1_queda_degradado() -> None:
    """D1 sigue vivo y latiendo, pero no puede detectar nada: el sistema no esta
    haciendo su trabajo y el informe lo dice, con el motivo."""
    from app.deteccion.d1 import MOTIVO_SIN_REGLAS
    from app.supervision import EstadoSupervision, EstadoTarea

    repo = RepoDeteccionFalso([_evento(1, "TXN-001")], reglas=[])
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(repo, supervisor, ajustes=AJUSTES, reloj=lambda: AHORA))
    try:
        await repo.esperar_procesado(supervisor)
        informe = supervisor.informe()
    finally:
        await supervisor.detener()

    fila = informe.tarea(NOMBRE_D1)
    assert fila.estado == EstadoTarea.DEGRADADA
    assert fila.advertencia == MOTIVO_SIN_REGLAS
    assert informe.estado == EstadoSupervision.DEGRADADO
    assert repo.alertas == {}


@pytest.mark.asyncio
async def prueba_con_reglas_la_advertencia_se_limpia() -> None:
    from app.supervision import EstadoTarea

    fila = await _correr(RepoDeteccionFalso([_evento(1, "TXN-001")]))

    assert fila.estado == EstadoTarea.CORRIENDO
    assert fila.advertencia is None


# ---------------------------------------------------------------------------
# Rezago (T-57)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_d1_publica_el_rezago_medido_con_su_antiguedad() -> None:
    repo = RepoDeteccionFalso([_evento(1, "TXN-001")])
    repo.rezago = RezagoMedido(pendientes=1200, mas_vieja=datetime(2026, 9, 25, 4, 45, 5, tzinfo=UTC))

    informe = await _correr(repo)

    assert informe.rezago is not None
    assert informe.rezago.pendientes == 1200
    assert informe.rezago.antiguedad_segundos == 120.0
    assert informe.rezago.medido_en == AHORA


@pytest.mark.asyncio
async def prueba_sin_pendientes_no_hay_antiguedad() -> None:
    repo = RepoDeteccionFalso([_evento(1, "TXN-001")])

    informe = await _correr(repo)

    assert informe.rezago is not None
    assert informe.rezago.pendientes == 0
    assert informe.rezago.antiguedad_segundos is None


@pytest.mark.asyncio
async def prueba_la_medicion_se_espacia_por_el_intervalo_configurado() -> None:
    """Cuatro eventos seguidos, un solo intervalo de 2 s: se mide una vez, no cuatro."""
    repo = RepoDeteccionFalso([_evento(n, "TXN-002") for n in range(1, 5)])

    await _correr(repo)

    assert repo.mediciones_rezago == 1


@pytest.mark.asyncio
async def prueba_una_medicion_que_falla_no_detiene_la_deteccion() -> None:
    repo = RepoDeteccionFalso([_evento(1, "TXN-001"), _evento(2, "TXN-002")])
    repo.fallar_rezago = True

    informe = await _correr(repo)

    assert repo.bitacora == ["alerta:TXN-001", "token:TXN-001", "token:TXN-002"]
    assert informe.rezago is None
    assert informe.ultimo_error is None


# ---------------------------------------------------------------------------
# Atraso en segundos (T-69): la red de seguridad que no depende del orden de los ids
# ---------------------------------------------------------------------------


def _evento_insertado(numero: int, transaccion_id: str, atras_segundos: float) -> EventoTransaccion:
    return EventoTransaccion(
        token=token(numero),
        documento=_documento(transaccion_id),
        insertada_en=AHORA - timedelta(seconds=atras_segundos),
    )


async def _correr_midiendo_siempre(repo: RepoDeteccionFalso) -> InformeTarea:
    """Como `_correr`, pero midiendo en cada vuelta: la ultima vuelta decide el atraso."""
    ajustes = AJUSTES.model_copy(update={"rezago_intervalo_segundos": 1e-9})
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(repo, supervisor, ajustes=ajustes, reloj=lambda: AHORA))
    try:
        await repo.esperar_procesado(supervisor)
        return supervisor.informe().tarea(NOMBRE_D1)
    finally:
        await supervisor.detener()


@pytest.mark.asyncio
async def prueba_el_atraso_es_ahora_menos_la_insercion_del_ultimo_evento() -> None:
    repo = RepoDeteccionFalso([_evento_insertado(1, "TXN-001", 40.0)])

    informe = await _correr(repo)

    assert informe.rezago is not None
    assert informe.rezago.atraso_segundos == 40.0
    assert informe.rezago.pendientes == 0, "el conteo puede decir 0 y el atraso no"


@pytest.mark.asyncio
async def prueba_la_vuelta_vacia_deja_el_atraso_en_cero() -> None:
    repo = RepoDeteccionFalso([_evento_insertado(1, "TXN-001", 40.0), _evento(2, None)])

    informe = await _correr_midiendo_siempre(repo)

    assert informe.rezago is not None
    assert informe.rezago.atraso_segundos == 0.0


@pytest.mark.asyncio
async def prueba_un_evento_sin_hora_de_insercion_no_inventa_atraso() -> None:
    repo = RepoDeteccionFalso([_evento(1, "TXN-001")])

    informe = await _correr(repo)

    assert informe.rezago is not None
    assert informe.rezago.atraso_segundos is None


def prueba_un_desfase_negativo_cuenta_como_cero() -> None:
    from app.deteccion.d1 import atraso_segundos

    assert atraso_segundos(AHORA + timedelta(seconds=3), AHORA) == 0.0
    assert atraso_segundos(AHORA - timedelta(seconds=2.26), AHORA) == 2.3
