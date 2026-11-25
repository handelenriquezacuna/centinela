"""T-62 · D1 por lotes probado sin base de datos, con el doble del repositorio.

Lo que se sostiene aqui:

- el token se guarda UNA vez por lote, despues de todas las alertas, y es el del ultimo evento;
- un lote nunca pasa de `lote_maximo`, y un evento solo sale solo (no espera a llenar);
- las lecturas son una por lote, no una por transaccion;
- un evento envenenado se aisla: va al descarte y el resto del lote se procesa;
- un lote que se reprocesa (cayo antes del token) no duplica alertas;
- latido, contadores y atraso siguen publicandose por evento.

La equivalencia contra Mongo (mismas alertas por evento y por lote) y la caida a mitad de
lote con base de verdad estan en `pruebas/integracion/prueba_d1_lotes_mongo.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.deteccion.d1 import NOMBRE_D1, d1
from app.nucleo.config import AjustesDeteccion
from app.repos.deteccion import EventoTransaccion
from app.supervision import InformeTarea, Supervisor
from pruebas.dobles.datos_demo import TRANSACCIONES
from pruebas.dobles.repo_deteccion_falso import RepoDeteccionFalso, token

AHORA = datetime(2026, 9, 25, 4, 47, 5, tzinfo=UTC)


def _ajustes(lote_maximo: int = 200) -> AjustesDeteccion:
    return AjustesDeteccion(
        lote_maximo=lote_maximo,
        espera_cursor_ms=10,
        reintentos=2,
        espera_inicial_segundos=0.001,
        espera_maxima_segundos=0.004,
    )


def _documento(transaccion_id: str) -> dict[str, Any]:
    transaccion = next(t for t in TRANSACCIONES if t.transaccion_id == transaccion_id)
    return transaccion.a_documento()


def _evento(numero: int, transaccion_id: str, atras: float | None = None) -> EventoTransaccion:
    return EventoTransaccion(
        token=token(numero),
        documento=_documento(transaccion_id),
        insertada_en=None if atras is None else AHORA - timedelta(seconds=atras),
    )


async def _correr(repo: RepoDeteccionFalso, lote_maximo: int = 200) -> InformeTarea:
    ajustes = _ajustes(lote_maximo).model_copy(update={"rezago_intervalo_segundos": 1e-9})
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(NOMBRE_D1, d1(repo, supervisor, ajustes=ajustes, reloj=lambda: AHORA))
    try:
        await repo.esperar_procesado(supervisor)
        return supervisor.informe().tarea(NOMBRE_D1)
    finally:
        await supervisor.detener()


# ---------------------------------------------------------------------------
# Orden de escrituras y token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_un_lote_guarda_un_solo_token_despues_de_las_alertas() -> None:
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001"), _evento(2, "TXN-002"), _evento(3, "TXN-002")]
    )

    informe = await _correr(repo)

    assert repo.bitacora == ["alerta:TXN-001", "token:TXN-002"]
    assert repo.token_guardado == token(3), "el token es el del ultimo evento del lote"
    assert informe.eventos_procesados == 3, "cada evento del lote cuenta"


@pytest.mark.asyncio
async def prueba_las_lecturas_son_una_por_lote() -> None:
    repo = RepoDeteccionFalso([_evento(n, "TXN-002") for n in range(1, 6)])

    await _correr(repo)

    assert repo.bitacora_lecturas == ["origenes:5", "contextos:5"]


@pytest.mark.asyncio
async def prueba_un_lote_nunca_pasa_del_maximo() -> None:
    repo = RepoDeteccionFalso([_evento(n, "TXN-002") for n in range(1, 6)])

    await _correr(repo, lote_maximo=2)

    assert repo.bitacora == ["token:TXN-002", "token:TXN-002", "token:TXN-002"]
    assert repo.bitacora_lecturas == [
        "origenes:2",
        "contextos:2",
        "origenes:2",
        "contextos:2",
    ], "el quinto evento sale solo, por el camino por evento"


@pytest.mark.asyncio
async def prueba_un_evento_solo_sale_de_inmediato() -> None:
    """Con poco trafico el lote es de uno y no espera a llenarse."""
    repo = RepoDeteccionFalso([_evento(1, "TXN-001")])

    await _correr(repo)

    assert repo.bitacora == ["alerta:TXN-001", "token:TXN-001"]
    assert repo.bitacora_lecturas == [], "el evento solo usa el camino por evento"


# ---------------------------------------------------------------------------
# Aislamiento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_un_evento_malformado_se_aisla_y_el_resto_del_lote_se_procesa() -> None:
    malformada = _documento("TXN-001") | {"_id": "TXN-009", "monto_crc": 750_000.0}
    repo = RepoDeteccionFalso(
        [
            _evento(1, "TXN-001"),
            EventoTransaccion(token=token(2), documento=malformada),
            _evento(3, "TXN-002"),
        ]
    )

    informe = await _correr(repo)

    assert repo.bitacora == ["alerta:TXN-001", "descarte:TXN-009", "token:TXN-002"]
    assert repo.descartes[0]["intentos"] == 1
    assert list(repo.alertas) == ["TXN-001"]
    assert informe.ultimo_error is not None
    assert informe.eventos_procesados == 3


@pytest.mark.asyncio
async def prueba_un_evento_sin_cuenta_de_origen_se_reintenta_solo_y_se_descarta() -> None:
    sana = _documento("TXN-001")
    rota = sana | {"_id": "TXN-008", "cuenta_origen": "CTA-404"}
    repo = RepoDeteccionFalso(
        [
            _evento(1, "TXN-001"),
            EventoTransaccion(token=token(2), documento=rota),
            _evento(3, "TXN-002"),
        ]
    )
    repo.origenes_rotos["CTA-404"] = LookupError("la cuenta de origen CTA-404 no existe")

    await _correr(repo)

    assert repo.bitacora == ["alerta:TXN-001", "descarte:TXN-008", "token:TXN-002"]
    # 1 intento + 2 reintentos, como un evento solo.
    assert repo.descartes[0]["intentos"] == 3
    assert "LookupError" in repo.descartes[0]["motivo"]


@pytest.mark.asyncio
async def prueba_si_falla_la_escritura_del_lote_se_procesa_evento_por_evento() -> None:
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001"), _evento(2, "TXN-002")], fallos_al_insertar=1
    )

    await _correr(repo)

    # El lote entero fallo una vez; cada evento se reintenta solo y la alerta queda.
    assert repo.bitacora == ["fallo_insertar_lote", "alerta:TXN-001", "token:TXN-002"]
    assert repo.descartes == []


# ---------------------------------------------------------------------------
# Reproceso
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_reprocesar_un_lote_no_duplica_alertas() -> None:
    """El proceso cayo con las alertas escritas y sin token: el lote vuelve entero."""
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001"), _evento(2, "TXN-002")], alertas_existentes=["TXN-001"]
    )

    informe = await _correr(repo)

    assert repo.bitacora == ["duplicada:TXN-001", "token:TXN-002"]
    assert repo.alertas == {}
    assert informe.ultimo_error is None


# ---------------------------------------------------------------------------
# Latencia, atraso y rezago
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_la_latencia_se_mide_por_alerta_desde_la_insercion() -> None:
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001", atras=2.0), _evento(2, "TXN-002", atras=1.0)]
    )
    supervisor = Supervisor(tolerancia_segundos=30)
    supervisor.lanzar(
        NOMBRE_D1, d1(repo, supervisor, ajustes=_ajustes(), reloj=lambda: AHORA)
    )
    try:
        await repo.esperar_procesado(supervisor)
        latencia = supervisor.informe().tarea(NOMBRE_D1).latencia
    finally:
        await supervisor.detener()

    assert latencia is not None
    assert latencia.n == 1, "TXN-002 no tiene alerta: nada que medir"
    assert latencia.max == 2000.0


@pytest.mark.asyncio
async def prueba_el_atraso_es_el_del_ultimo_evento_del_lote() -> None:
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001", atras=90.0), _evento(2, "TXN-002", atras=40.0)]
    )

    informe = await _correr(repo)

    assert informe.rezago is not None
    assert informe.rezago.atraso_segundos == 40.0


@pytest.mark.asyncio
async def prueba_la_vuelta_vacia_despues_de_un_lote_deja_el_atraso_en_cero() -> None:
    repo = RepoDeteccionFalso(
        [
            _evento(1, "TXN-001", atras=90.0),
            _evento(2, "TXN-002", atras=40.0),
            EventoTransaccion(token=token(3), documento=None),
        ]
    )

    informe = await _correr(repo)

    assert informe.rezago is not None
    assert informe.rezago.atraso_segundos == 0.0
    assert repo.bitacora == ["alerta:TXN-001", "token:TXN-002"]
    assert informe.eventos_procesados == 3


@pytest.mark.asyncio
async def prueba_un_error_del_cursor_llega_despues_de_los_eventos_previos() -> None:
    repo = RepoDeteccionFalso(
        [_evento(1, "TXN-001"), _evento(2, "TXN-002")],
        token_guardado=token(99),
        rechazar_token=True,
    )

    informe = await _correr(repo)

    assert repo.aperturas == [token(99), None]
    assert repo.bitacora == ["alerta:TXN-001", "token:TXN-002"]
    assert informe.ultimo_error is not None
    assert "ChangeStreamHistoryLost" in informe.ultimo_error.mensaje


def prueba_la_configuracion_acepta_el_lote_maximo() -> None:
    assert AjustesDeteccion().lote_maximo == 200
    with pytest.raises(ValueError):
        AjustesDeteccion(lote_maximo=0)
