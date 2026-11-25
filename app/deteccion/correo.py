"""H-11 · El correo de una alerta CRITICA: armado (puro) y envio por SMTP.

`armar_correo` es una funcion pura: recibe la alerta ya validada y la configuracion, y
devuelve el mensaje. Se prueba sin red ni base. `enviar_por_smtp` es la unica parte con
red; es sincrona (smtplib no tiene version asincrona) y D2 la corre en un hilo con
`asyncio.to_thread` para no bloquear el bucle de eventos.

El correo se entrega AL MENOS UNA VEZ, y el cuerpo lo dice. Si D2 muere despues de enviar
y antes de anotar el resultado, al reanudar vuelve a enviar. El `Message-ID` es fijo por
alerta (`<NOT-000123@...>`), asi que quien reciba un repetido lo puede reconocer.

Este modulo no importa `app/api/`: los formatos de colones y hora de Costa Rica se
reimplementan aqui (son tres lineas) para no acoplar el motor a la capa HTTP.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import format_datetime

from app.modelos import ZONA_COSTA_RICA, Alerta, ReglaAplicada
from app.nucleo.config import AjustesNotificaciones

_ETIQUETA_SEVERIDAD = {"CRITICA": "Crítica", "ALTA": "Alta", "MEDIA": "Media"}

AVISO_AL_MENOS_UNA_VEZ = (
    "Este aviso se entrega al menos una vez: si el servicio se reinicia mientras lo "
    "envia, puede llegar repetido (el Message-ID es el mismo)."
)


def colones(monto: int) -> str:
    """Colones enteros como `₡280.000`: punto de miles, sin centimos."""
    signo = "-" if monto < 0 else ""
    return f"{signo}₡{abs(monto):,}".replace(",", ".")


def notificacion_id_para(alerta_id: str) -> str:
    """`ALR-000123` -> `NOT-000123`. Derivado, para que reprocesar la misma alerta choque."""
    if not alerta_id.startswith("ALR-"):
        raise ValueError(f"identificador de alerta inesperado: {alerta_id}")
    return "NOT-" + alerta_id.removeprefix("ALR-")


def asunto_de(alerta: Alerta) -> str:
    return (
        f"[Centinela] Alerta crítica {alerta.alerta_id}: {colones(alerta.monto_crc)} "
        f"de {alerta.cliente_nombre}"
    )


def enlace_al_detalle(alerta_id: str, portal_url: str) -> str:
    return f"{portal_url.rstrip('/')}/detalle/{alerta_id}"


def _linea_de_regla(regla: ReglaAplicada | str) -> str:
    if isinstance(regla, str):  # alerta v1: solo el identificador de la regla
        return f"  - {regla}"
    return f"  - {regla.codigo} v{regla.version} ({regla.peso} puntos): {regla.explicacion}"


def cuerpo_de(alerta: Alerta, portal_url: str) -> str:
    """El cuerpo en texto plano, en espanol, con la hora de Costa Rica."""
    creada = alerta.fecha_creacion.astimezone(ZONA_COSTA_RICA).strftime("%d/%m/%Y %H:%M")
    reglas = "\n".join(_linea_de_regla(regla) for regla in alerta.reglas_disparadas)
    severidad = _ETIQUETA_SEVERIDAD.get(str(alerta.severidad), str(alerta.severidad))
    return (
        f"Centinela detectó una alerta de severidad crítica.\n"
        f"\n"
        f"Alerta: {alerta.alerta_id}\n"
        f"Cliente: {alerta.cliente_nombre}\n"
        f"Monto: {colones(alerta.monto_crc)}\n"
        f"Cuenta de origen: {alerta.cuenta_origen}\n"
        f"Cuenta de destino: {alerta.cuenta_destino}\n"
        f"Severidad: {severidad} (puntaje {alerta.puntaje} de 100)\n"
        f"Creada: {creada} (hora de Costa Rica)\n"
        f"\n"
        f"Reglas disparadas:\n"
        f"{reglas}\n"
        f"\n"
        f"Ver el detalle y resolver: {enlace_al_detalle(alerta.alerta_id, portal_url)}\n"
        f"\n"
        f"{AVISO_AL_MENOS_UNA_VEZ}\n"
    )


def armar_correo(alerta: Alerta, ajustes: AjustesNotificaciones) -> EmailMessage:
    """El mensaje completo de una alerta: remitente, destinatarios, asunto y cuerpo.

    `Date` sale de la creacion de la alerta, no del momento del envio: un reintento o un
    reproceso produce el mismo mensaje.
    """
    mensaje = EmailMessage()
    mensaje["From"] = ajustes.remitente
    mensaje["To"] = ", ".join(ajustes.destinatarios)
    mensaje["Subject"] = asunto_de(alerta)
    mensaje["Date"] = format_datetime(alerta.fecha_creacion)
    dominio = ajustes.remitente.rpartition("@")[2] or "centinela.local"
    mensaje["Message-ID"] = f"<{notificacion_id_para(alerta.alerta_id)}@{dominio}>"
    mensaje["X-Centinela-Alerta"] = alerta.alerta_id
    mensaje.set_content(cuerpo_de(alerta, ajustes.portal_url), charset="utf-8")
    return mensaje


def enviar_por_smtp(
    mensaje: EmailMessage, *, host: str, puerto: int, espera_segundos: float
) -> None:
    """Envia el mensaje al servidor SMTP. Sincrona: D2 la corre con `asyncio.to_thread`.

    Sin TLS ni credenciales a proposito: es el servidor de pruebas (Mailpit). Cualquier
    fallo (puerto cerrado, tiempo agotado, destinatario rechazado) sube como excepcion y
    D2 lo registra en la notificacion; no tumba nada.
    """
    with smtplib.SMTP(host, puerto, timeout=espera_segundos) as servidor:
        servidor.send_message(mensaje)
