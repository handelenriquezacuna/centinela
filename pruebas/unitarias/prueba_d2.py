"""H-11 · D2 probado sin base de datos: el orden de las escrituras, los reprocesos y los fallos.

Cada prueba usa `RepoNotificacionesFalso`, que anota cada escritura en el orden en que D2
la pidio:

- la notificacion se escribe ANTES que el token, y el token solo cuando el evento termino;
- una alerta que no es CRITICA no toca ni el correo ni la base;
- un reproceso (la notificacion ya existe) no envia otra vez, o retoma los intentos que
  faltaban si D2 murio entre intentos;
- un correo que falla se reintenta con espera progresiva hasta el tope, queda `fallido` y
  D2 sigue con la alerta siguiente: nunca se reintenta sin fin ni tumba el proceso;
- lo que no es un fallo de correo (la base, un documento invalido) se reintenta y va al
  descarte;
- un token inservible resincroniza desde ahora.

Contra Mongo y SMTP de verdad, el mismo contrato lo prueban `pruebas/integracion/prueba_d2_mongo.py`
y `prueba_d2_mailpit.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import pytest

from app.deteccion.d2 import (
    NOMBRE_D2,
    Resultado,
    d2,
    documento_pendiente,
    espera_progresiva,
    procesar_alerta,
)
from app.modelos import Alerta, Severidad
from app.nucleo.config import AjustesNotificaciones
from app.repos.notificaciones import EventoAlerta, ProtocoloRepoNotificaciones
from app.supervision import Supervisor
from pruebas.dobles.alertas_d2 import (
    RepoNotificacionesFalso,
    alerta_documento,
    token,
    vuelta,
)

AJUSTES = AjustesNotificaciones(
    espera_cursor_ms=10,
    reintentos_envio=3,
    espera_inicial_segundos=0.5,
    espera_maxima_segundos=1.5,
)
AHORA = datetime(2026, 9, 25, 4, 47, 5, tzinfo=UTC)


class Correo:
    """Enviador de prueba: anota lo enviado y falla las veces que se le pida."""

    def __init__(self, *, fallos: int = 0, siempre_falla: bool = False) -> None:
        self.enviados: list[EmailMessage] = []
        self.intentos = 0
        self._fallos = fallos
        self._siempre = siempre_falla

    async def __call__(self, mensaje: EmailMessage) -> None:
        self.intentos += 1
        if self._siempre or self._fallos > 0:
            self._fallos -= 1
            raise ConnectionRefusedError("[Errno 61] Connection refused")
        self.enviados.append(mensaje)


class Esperas:
    """`esperar` de prueba: no duerme y anota cuanto se pidio esperar."""

    def __init__(self) -> None:
        self.pedidas: list[float] = []

    async def __call__(self, segundos: float) -> None:
        self.pedidas.append(segundos)
        await asyncio.sleep(0)


async def _procesar(
    repo: RepoNotificacionesFalso,
    documento: dict[str, Any],
    correo: Correo,
    esperas: Esperas | None = None,
    fallos_vistos: list[BaseException] | None = None,
) -> Resultado:
    return await procesar_alerta(
        repo,
        documento,
        ajustes=AJUSTES,
        enviar=correo,
        reloj=lambda: AHORA,
        esperar=esperas or Esperas(),
        al_fallar_envio=fallos_vistos.append if fallos_vistos is not None else None,
    )


# ---------------------------------------------------------------------------
# Un evento
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_una_alerta_critica_escribe_la_notificacion_y_envia_el_correo() -> None:
    repo, correo = RepoNotificacionesFalso([]), Correo()

    resultado = await _procesar(repo, alerta_documento(7), correo)

    assert resultado is Resultado.ENVIADA
    assert len(correo.enviados) == 1
    assert correo.enviados[0]["X-Centinela-Alerta"] == "ALR-000007"
    assert repo.bitacora == ["pendiente:NOT-000007", "enviado:NOT-000007:1"]
    nota = repo.notificaciones["NOT-000007"]
    assert nota["alerta_id"] == "ALR-000007"
    assert nota["canal"] == "correo"
    assert nota["estado_envio"] == "enviado"
    assert nota["intentos"] == 1
    assert nota["destinatarios"] == AJUSTES.destinatarios
    assert nota["asunto"].startswith("[Centinela] Alerta crítica ALR-000007")
    assert "detalle_error" not in nota


@pytest.mark.asyncio
@pytest.mark.parametrize("severidad", [Severidad.ALTA, Severidad.MEDIA])
async def prueba_una_alerta_que_no_es_critica_no_produce_nada(severidad: Severidad) -> None:
    repo, correo = RepoNotificacionesFalso([]), Correo()

    resultado = await _procesar(repo, alerta_documento(severidad=severidad, puntaje=60), correo)

    assert resultado is Resultado.IGNORADA
    assert repo.bitacora == []
    assert correo.intentos == 0


@pytest.mark.asyncio
async def prueba_el_documento_pendiente_cumple_el_contrato_de_campos() -> None:
    alerta = Alerta.model_validate(alerta_documento(3))
    from app.deteccion.correo import armar_correo

    documento = documento_pendiente(alerta, armar_correo(alerta, AJUSTES), AJUSTES, AHORA)

    assert set(documento) == {
        "_id", "alerta_id", "canal", "estado_envio", "fecha", "intentos",
        "asunto", "destinatarios", "esquema_version",
    }
    assert documento["_id"] == "NOT-000003"
    assert documento["estado_envio"] == "pendiente"
    assert documento["intentos"] == 0
    assert documento["fecha"] == AHORA


@pytest.mark.asyncio
async def prueba_reprocesar_una_enviada_no_envia_otro_correo() -> None:
    repo, correo = RepoNotificacionesFalso([]), Correo()
    await _procesar(repo, alerta_documento(7), correo)

    resultado = await _procesar(repo, alerta_documento(7), correo)

    assert resultado is Resultado.YA_PROCESADA
    assert len(correo.enviados) == 1, "el indice unico rechazo el duplicado y no se envio de nuevo"
    assert repo.bitacora[-1] == "duplicada:NOT-000007"


@pytest.mark.asyncio
async def prueba_reprocesar_una_fallida_tampoco_envia() -> None:
    """Agotada, queda `fallido`: reprocesar el evento no la reintenta sin fin."""
    repo, correo = RepoNotificacionesFalso([]), Correo(siempre_falla=True)
    await _procesar(repo, alerta_documento(7), correo)
    intentos_antes = correo.intentos

    resultado = await _procesar(repo, alerta_documento(7), correo)

    assert resultado is Resultado.YA_PROCESADA
    assert correo.intentos == intentos_antes


@pytest.mark.asyncio
async def prueba_una_pendiente_de_una_caida_retoma_los_intentos_que_faltaban() -> None:
    """D2 murio despues del intento 1: al reanudar quedan 2 de los 3, no 3 mas."""
    existentes = {
        "NOT-000007": {
            "_id": "NOT-000007", "alerta_id": "ALR-000007", "canal": "correo",
            "estado_envio": "pendiente", "intentos": 1, "fecha": AHORA,
            "detalle_error": "ConnectionRefusedError: ...", "esquema_version": 1,
        }
    }
    repo, correo = RepoNotificacionesFalso([], existentes=existentes), Correo(siempre_falla=True)

    resultado = await _procesar(repo, alerta_documento(7), correo)

    assert resultado is Resultado.FALLIDA
    assert correo.intentos == 2
    assert repo.notificaciones["NOT-000007"]["intentos"] == 3
    assert repo.notificaciones["NOT-000007"]["estado_envio"] == "fallido"


@pytest.mark.asyncio
async def prueba_una_pendiente_que_ya_agoto_el_tope_se_cierra_sin_reenviar() -> None:
    existentes = {
        "NOT-000007": {
            "_id": "NOT-000007", "alerta_id": "ALR-000007", "canal": "correo",
            "estado_envio": "pendiente", "intentos": 3, "fecha": AHORA, "esquema_version": 1,
        }
    }
    repo, correo = RepoNotificacionesFalso([], existentes=existentes), Correo()

    resultado = await _procesar(repo, alerta_documento(7), correo)

    assert resultado is Resultado.FALLIDA
    assert correo.intentos == 0
    assert repo.notificaciones["NOT-000007"]["estado_envio"] == "fallido"
    assert "reintentos agotados" in repo.notificaciones["NOT-000007"]["detalle_error"]


# ---------------------------------------------------------------------------
# Fallos del correo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_un_correo_que_falla_se_reintenta_con_espera_progresiva_y_se_recupera() -> None:
    repo, correo, esperas = RepoNotificacionesFalso([]), Correo(fallos=2), Esperas()

    resultado = await _procesar(repo, alerta_documento(7), correo, esperas)

    assert resultado is Resultado.ENVIADA
    assert correo.intentos == 3
    assert esperas.pedidas == [0.5, 1.0], "se duplica entre intentos"
    assert repo.bitacora == [
        "pendiente:NOT-000007",
        "pendiente:NOT-000007:1",
        "pendiente:NOT-000007:2",
        "enviado:NOT-000007:3",
    ]
    nota = repo.notificaciones["NOT-000007"]
    assert nota["estado_envio"] == "enviado"
    assert nota["intentos"] == 3
    assert "detalle_error" not in nota, "el exito borra el motivo de los intentos fallidos"


@pytest.mark.asyncio
async def prueba_un_correo_que_nunca_sale_queda_fallido_con_su_motivo_y_sin_excepcion() -> None:
    repo, correo, esperas = RepoNotificacionesFalso([]), Correo(siempre_falla=True), Esperas()
    vistos: list[BaseException] = []

    resultado = await _procesar(repo, alerta_documento(7), correo, esperas, vistos)

    assert resultado is Resultado.FALLIDA
    assert correo.intentos == AJUSTES.reintentos_envio, "el tope corta: no se reintenta sin fin"
    assert esperas.pedidas == [0.5, 1.0], "no espera despues del ultimo intento"
    nota = repo.notificaciones["NOT-000007"]
    assert nota["estado_envio"] == "fallido"
    assert nota["intentos"] == 3
    assert nota["detalle_error"].startswith("ConnectionRefusedError")
    assert len(vistos) == 3, "cada fallo se le cuenta al supervisor"


def prueba_la_espera_se_duplica_hasta_el_tope() -> None:
    assert [espera_progresiva(n, AJUSTES) for n in range(4)] == [0.5, 1.0, 1.5, 1.5]


# ---------------------------------------------------------------------------
# El bucle
# ---------------------------------------------------------------------------


async def _correr(
    repo: RepoNotificacionesFalso,
    correo: Correo,
    *,
    ajustes: AjustesNotificaciones = AJUSTES,
) -> tuple[Supervisor, asyncio.Task[None]]:
    supervisor = Supervisor(tolerancia_segundos=30)
    esperas = Esperas()
    tarea = supervisor.lanzar(
        NOMBRE_D2,
        d2(repo, supervisor, ajustes=ajustes, enviar=correo, reloj=lambda: AHORA, esperar=esperas),
    )
    return supervisor, tarea


@pytest.mark.asyncio
async def prueba_cumple_el_protocolo_del_repositorio() -> None:
    assert isinstance(RepoNotificacionesFalso([]), ProtocoloRepoNotificaciones)


@pytest.mark.asyncio
async def prueba_la_notificacion_se_escribe_antes_que_el_token() -> None:
    repo = RepoNotificacionesFalso([vuelta(1), vuelta(2)])
    supervisor, _ = await _correr(repo, Correo())

    await repo.esperar_procesado(supervisor)
    await supervisor.detener()

    assert repo.bitacora == [
        "pendiente:NOT-000001", "enviado:NOT-000001:1", "token:TXN-000001",
        "pendiente:NOT-000002", "enviado:NOT-000002:1", "token:TXN-000002",
    ]
    assert repo.token_guardado == token(2)


@pytest.mark.asyncio
async def prueba_un_correo_fallido_no_detiene_a_d2_y_la_siguiente_se_procesa() -> None:
    repo = RepoNotificacionesFalso([vuelta(1), vuelta(2)])
    correo = Correo(fallos=3)  # los tres intentos de la primera; la segunda sale
    supervisor, _ = await _correr(repo, correo)

    await repo.esperar_procesado(supervisor)
    fila = supervisor.informe().tarea(NOMBRE_D2)
    await supervisor.detener()

    assert repo.notificaciones["NOT-000001"]["estado_envio"] == "fallido"
    assert repo.notificaciones["NOT-000002"]["estado_envio"] == "enviado"
    assert repo.token_guardado == token(2), "el token avanzo tambien sobre la fallida"
    assert fila.estado == "corriendo"
    assert "ConnectionRefusedError" in fila.ultimo_error.mensaje, "el fallo es visible en /estado"
    assert fila.eventos_procesados == 2


@pytest.mark.asyncio
async def prueba_reanuda_con_el_token_guardado_y_no_reprocesa_lo_anterior() -> None:
    repo = RepoNotificacionesFalso([vuelta(1), vuelta(2), vuelta(3)], token_guardado=token(2))
    correo = Correo()
    supervisor, _ = await _correr(repo, correo)

    await repo.esperar_procesado(supervisor)
    await supervisor.detener()

    assert repo.aperturas == [token(2)]
    assert [m["X-Centinela-Alerta"] for m in correo.enviados] == ["ALR-000003"]


@pytest.mark.asyncio
async def prueba_si_muere_entre_la_notificacion_y_el_token_el_reproceso_no_duplica() -> None:
    """Murio despues de enviar y antes del token: el token guardado es el del evento 1."""
    existentes = {
        "NOT-000002": {
            "_id": "NOT-000002", "alerta_id": "ALR-000002", "canal": "correo",
            "estado_envio": "enviado", "intentos": 1, "fecha": AHORA, "esquema_version": 1,
        }
    }
    repo = RepoNotificacionesFalso(
        [vuelta(1), vuelta(2), vuelta(3)], token_guardado=token(1), existentes=existentes
    )
    correo = Correo()
    supervisor, _ = await _correr(repo, correo)

    await repo.esperar_procesado(supervisor)
    await supervisor.detener()

    # El evento 2 se reprocesa (esa es la entrega al menos una vez del change stream)...
    assert "duplicada:NOT-000002" in repo.bitacora
    # ...pero no manda otro correo: solo sale el del evento 3.
    assert [m["X-Centinela-Alerta"] for m in correo.enviados] == ["ALR-000003"]


@pytest.mark.asyncio
async def prueba_una_vuelta_vacia_late_y_sin_token_guarda_el_de_la_vuelta() -> None:
    repo = RepoNotificacionesFalso([EventoAlerta(token=token(9), documento=None)])
    supervisor, _ = await _correr(repo, Correo())

    await repo.esperar_procesado(supervisor)
    fila = supervisor.informe().tarea(NOMBRE_D2)
    await supervisor.detener()

    assert fila.eventos_procesados == 1
    assert repo.token_guardado == token(9)
    assert repo.bitacora == ["token:None"]


@pytest.mark.asyncio
async def prueba_una_vuelta_vacia_no_reescribe_el_token_antes_del_refresco() -> None:
    repo = RepoNotificacionesFalso(
        [EventoAlerta(token=token(9), documento=None), EventoAlerta(token=token(10), documento=None)]
    )
    supervisor, _ = await _correr(repo, Correo())

    await repo.esperar_procesado(supervisor)
    await supervisor.detener()

    assert repo.token_guardado == token(9), "el refresco es cada refresco_reanudacion_segundos"


@pytest.mark.asyncio
async def prueba_un_token_inservible_resincroniza_desde_ahora() -> None:
    repo = RepoNotificacionesFalso([vuelta(1)], token_guardado=token(0), rechazar_token=True)
    correo = Correo()
    supervisor, _ = await _correr(repo, correo)

    await repo.esperar_procesado(supervisor)
    fila = supervisor.informe().tarea(NOMBRE_D2)
    await supervisor.detener()

    assert repo.aperturas == [token(0), None], "se reabre sin token"
    assert "ya no sirve" in fila.ultimo_error.mensaje
    assert len(correo.enviados) == 1


@pytest.mark.asyncio
async def prueba_una_alerta_invalida_va_al_descarte_y_d2_sigue() -> None:
    envenenada = EventoAlerta(
        token=token(1), documento={**alerta_documento(1), "puntaje": 500}  # fuera de 0..100
    )
    repo = RepoNotificacionesFalso([envenenada, vuelta(2)])
    supervisor, _ = await _correr(repo, Correo())

    await repo.esperar_procesado(supervisor)
    await supervisor.detener()

    assert [d["transaccion_id"] for d in repo.descartes] == ["TXN-000001"]
    assert "ValidationError" in repo.descartes[0]["motivo"]
    assert repo.notificaciones.keys() == {"NOT-000002"}
    assert repo.token_guardado == token(2)
    assert repo.bitacora.index("descarte:TXN-000001") < repo.bitacora.index("token:TXN-000001")


@pytest.mark.asyncio
async def prueba_un_fallo_de_la_base_se_reintenta_y_luego_se_procesa() -> None:
    repo = RepoNotificacionesFalso([vuelta(1)], fallos_al_crear=1)
    correo = Correo()
    supervisor, _ = await _correr(repo, correo)

    await repo.esperar_procesado(supervisor)
    await supervisor.detener()

    assert len(correo.enviados) == 1
    assert repo.descartes == []


@pytest.mark.asyncio
async def prueba_la_base_caida_agota_los_reintentos_y_descarta() -> None:
    repo = RepoNotificacionesFalso([vuelta(1)], fallos_al_crear=99)
    supervisor, _ = await _correr(repo, Correo())

    await repo.esperar_procesado(supervisor)
    await supervisor.detener()

    assert [d["transaccion_id"] for d in repo.descartes] == ["TXN-000001"]
    assert repo.descartes[0]["intentos"] == AJUSTES.reintentos_envio + 1


@pytest.mark.asyncio
async def prueba_si_ni_el_descarte_se_escribe_el_token_no_avanza_y_d2_sigue_vivo() -> None:
    repo = RepoNotificacionesFalso([vuelta(1)], fallos_al_crear=10**9, descarte_roto=True)
    supervisor, tarea = await _correr(repo, Correo())

    await asyncio.sleep(0.2)
    fila = supervisor.informe().tarea(NOMBRE_D2)
    await supervisor.detener()

    assert repo.token_guardado is None, "el token no avanza si el evento no termino"
    assert "ConnectionError" in fila.ultimo_error.mensaje
    assert not tarea.done() or tarea.cancelled()
