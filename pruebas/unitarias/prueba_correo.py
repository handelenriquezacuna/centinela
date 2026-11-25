"""H-11 · El correo de una alerta CRITICA: armado puro, filtro de severidad y SMTP real.

El armado y el filtro no tocan red ni base. El envio se prueba contra un servidor SMTP en
proceso (`pruebas/dobles/servidor_smtp.py`): `smtplib` habla con el de punta a punta, en un
hilo, como lo hace D2.
"""

from __future__ import annotations

import asyncio
import smtplib
import socket
from email.message import EmailMessage

import pytest
from pydantic import ValidationError

from app.deteccion.correo import (
    armar_correo,
    asunto_de,
    colones,
    cuerpo_de,
    enlace_al_detalle,
    enviar_por_smtp,
    notificacion_id_para,
)
from app.deteccion.d2 import enviador_smtp, es_notificable
from app.modelos import Alerta, Severidad
from app.nucleo.config import AjustesNotificaciones
from pruebas.dobles.alertas_d2 import alerta_documento
from pruebas.dobles.datos_demo import ALERTAS
from pruebas.dobles.servidor_smtp import ServidorSmtp

AJUSTES = AjustesNotificaciones(
    remitente="centinela@centinela.local",
    destinatarios=["guardia@centinela.local", "supervision@centinela.local"],
    portal_url="http://127.0.0.1:8000/",
)


def _alerta(**cambios: object) -> Alerta:
    return Alerta.model_validate(alerta_documento(**cambios))


# ---------------------------------------------------------------------------
# Armado
# ---------------------------------------------------------------------------


def prueba_colones_con_punto_de_miles_y_sin_centimos() -> None:
    assert colones(280_000) == "₡280.000"
    assert colones(0) == "₡0"
    assert colones(1_250_000) == "₡1.250.000"
    assert colones(999) == "₡999"


def prueba_el_asunto_nombra_alerta_monto_y_cliente() -> None:
    assert asunto_de(_alerta()) == (
        "[Centinela] Alerta crítica ALR-000001: ₡280.000 de Maria Rodriguez Vargas"
    )


def prueba_el_cuerpo_lleva_lo_que_la_guardia_necesita() -> None:
    cuerpo = cuerpo_de(_alerta(), AJUSTES.portal_url)

    assert "Alerta: ALR-000001" in cuerpo
    assert "Cliente: Maria Rodriguez Vargas" in cuerpo
    assert "Monto: ₡280.000" in cuerpo
    assert "Severidad: Crítica (puntaje 75 de 100)" in cuerpo
    assert "Cuenta de origen: 8712-4455" in cuerpo
    assert "Cuenta de destino: 6033-9001" in cuerpo
    # 04:47 UTC es 22:47 en Costa Rica (UTC-6, sin horario de verano).
    assert "Creada: 24/09/2026 22:47 (hora de Costa Rica)" in cuerpo
    assert "  - R-01 v1 (35 puntos): Monto de 280000 colones" in cuerpo
    assert "  - R-05 v2 (15 puntos): Transferencia a las 22:47" in cuerpo
    assert "http://127.0.0.1:8000/detalle/ALR-000001" in cuerpo
    # La entrega es al menos una vez, y el correo lo dice.
    assert "al menos una vez" in cuerpo


def prueba_el_enlace_no_duplica_la_barra_final() -> None:
    assert enlace_al_detalle("ALR-000007", "http://portal:8000/") == "http://portal:8000/detalle/ALR-000007"
    assert enlace_al_detalle("ALR-000007", "http://portal:8000") == "http://portal:8000/detalle/ALR-000007"


def prueba_el_mensaje_trae_remitente_destinatarios_y_message_id_fijo() -> None:
    mensaje = armar_correo(_alerta(), AJUSTES)

    assert mensaje["From"] == "centinela@centinela.local"
    assert mensaje["To"] == "guardia@centinela.local, supervision@centinela.local"
    # Fijo por alerta: quien reciba un repetido (entrega al menos una vez) lo reconoce.
    assert mensaje["Message-ID"] == "<NOT-000001@centinela.local>"
    assert mensaje["X-Centinela-Alerta"] == "ALR-000001"
    assert mensaje.get_content_type() == "text/plain"
    assert mensaje.get_content_charset() == "utf-8"
    # Armar dos veces produce el mismo mensaje: un reintento no cambia nada.
    assert armar_correo(_alerta(), AJUSTES).as_bytes() == mensaje.as_bytes()


def prueba_una_alerta_v1_tambien_arma_correo() -> None:
    """La demo y las alertas de antes de H-26 traen `reglas_disparadas` como REG-xxx."""
    v1 = next(a for a in ALERTAS if a.alerta_id == "ALR-001")

    cuerpo = cuerpo_de(v1, AJUSTES.portal_url)

    assert "  - REG-001" in cuerpo
    assert "Monto: ₡280.000" in cuerpo


def prueba_el_id_de_la_notificacion_sale_de_la_alerta() -> None:
    assert notificacion_id_para("ALR-000123") == "NOT-000123"
    assert notificacion_id_para("ALR-001") == "NOT-001"
    with pytest.raises(ValueError):
        notificacion_id_para("TXN-000123")


# ---------------------------------------------------------------------------
# Filtro de severidad
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("severidad", "notificable"),
    [(Severidad.CRITICA, True), (Severidad.ALTA, False), (Severidad.MEDIA, False)],
)
def prueba_solo_la_severidad_critica_se_notifica(severidad: Severidad, notificable: bool) -> None:
    assert es_notificable(alerta_documento(severidad=severidad)) is notificable


def prueba_un_documento_sin_severidad_no_se_notifica() -> None:
    assert es_notificable({}) is False


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------


def prueba_la_configuracion_exige_destinatario_y_tope_coherente() -> None:
    with pytest.raises(ValidationError):
        AjustesNotificaciones(destinatarios=[])
    with pytest.raises(ValidationError):
        AjustesNotificaciones(reintentos_envio=0)
    with pytest.raises(ValidationError):
        AjustesNotificaciones(espera_inicial_segundos=5, espera_maxima_segundos=1)
    with pytest.raises(ValidationError):
        AjustesNotificaciones(secreto="x")  # type: ignore[call-arg]


def prueba_la_configuracion_por_omision_es_la_de_la_modalidad_a() -> None:
    ajustes = AjustesNotificaciones()

    assert (ajustes.smtp_host, ajustes.smtp_puerto) == ("localhost", 1025)
    assert ajustes.activa is True


# ---------------------------------------------------------------------------
# SMTP de verdad, contra un servidor en proceso
# ---------------------------------------------------------------------------


def _mensaje() -> EmailMessage:
    return armar_correo(_alerta(), AJUSTES)


def prueba_smtplib_entrega_el_correo_completo_al_servidor() -> None:
    with ServidorSmtp() as servidor:
        enviar_por_smtp(_mensaje(), host="127.0.0.1", puerto=servidor.puerto, espera_segundos=5)
        recibidos = servidor.recibidos

    assert len(recibidos) == 1
    remitente, destinatarios, mensaje = recibidos[0]
    assert remitente.startswith("<centinela@centinela.local>")
    assert destinatarios == ("<guardia@centinela.local>", "<supervision@centinela.local>")
    assert str(mensaje["Subject"]) == "[Centinela] Alerta crítica ALR-000001: ₡280.000 de Maria Rodriguez Vargas"
    assert "₡280.000" in mensaje.get_content()


@pytest.mark.asyncio
async def prueba_el_enviador_corre_smtplib_fuera_del_bucle() -> None:
    """`asyncio.to_thread`: mientras se envia, el bucle sigue atendiendo otras tareas."""
    latidos: list[int] = []

    async def latir() -> None:
        while True:
            latidos.append(1)
            await asyncio.sleep(0.005)

    with ServidorSmtp() as servidor:
        tarea = asyncio.create_task(latir())
        await enviador_smtp(AjustesNotificaciones(smtp_host="127.0.0.1", smtp_puerto=servidor.puerto))(
            _mensaje()
        )
        tarea.cancel()
        assert len(servidor.recibidos) == 1

    assert latidos, "el bucle de eventos corrio mientras se enviaba"


def _puerto_cerrado() -> int:
    with socket.socket() as sonda:
        sonda.bind(("127.0.0.1", 0))
        return int(sonda.getsockname()[1])


def prueba_un_puerto_cerrado_falla_con_una_excepcion_y_no_cuelga() -> None:
    with pytest.raises(OSError):
        enviar_por_smtp(
            _mensaje(), host="127.0.0.1", puerto=_puerto_cerrado(), espera_segundos=2
        )


def prueba_un_servidor_que_rechaza_el_correo_falla_con_smtpexception() -> None:
    with ServidorSmtp(rechazar=True) as servidor, pytest.raises(smtplib.SMTPException):
        enviar_por_smtp(_mensaje(), host="127.0.0.1", puerto=servidor.puerto, espera_segundos=5)
