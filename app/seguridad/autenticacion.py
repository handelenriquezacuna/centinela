"""Comprobar usuario y contrasena. La unica puerta por la que se obtiene una sesion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.repos.agentes import ProtocoloRepoAgentes
from app.seguridad import contrasenas
from app.seguridad.bloqueo import (
    LimitadorIp,
    auditoria,
    bloqueo_vencido,
    esta_bloqueada,
)


async def autenticar(
    repo: ProtocoloRepoAgentes, agente_id: str, contrasena: str
) -> dict[str, Any] | None:
    """El documento del agente si las credenciales sirven; `None` en cualquier otro caso.

    No distingue "no existe", "esta inactivo", "no tiene credencial" y "contrasena
    mala": para quien llama es lo mismo, y asi tampoco se filtra cual es cual. El hash
    se calcula siempre (aun sin credencial) para que el tiempo tampoco lo delate.
    """
    agente = await repo.obtener(agente_id)
    credencial = agente.get("credencial") if agente else None
    correcta = contrasenas.verificar(contrasena, credencial)
    if agente is None or not correcta or not agente.get("activo", True):
        return None
    return agente


def _nombre_auditable(agente_id: str) -> str:
    """El identificador que alguien tecleo, acotado y entre comillas: no inyecta lineas al log."""
    return repr(agente_id[:64])


async def autenticar_con_limites(
    repo: ProtocoloRepoAgentes,
    limitador: LimitadorIp,
    ip: str,
    agente_id: str,
    contrasena: str,
    *,
    maximo: int,
    bloqueo_minutos: int,
    ahora: datetime | None = None,
) -> dict[str, Any] | None:
    """`autenticar` con el limite de intentos (T-83). `None` en cualquier fallo.

    Quien llama responde SIEMPRE lo mismo ante `None` (`MENSAJE_CREDENCIALES`): clave
    mala, cuenta inexistente, cuenta inactiva, cuenta bloqueada e IP frenada no se
    distinguen desde afuera. Por dentro:

    1. IP frenada: se rechaza sin tocar la base ni calcular el hash (no hay cuenta que
       delatar, y asi un ataque desde esa IP no gasta CPU).
    2. El hash se calcula SIEMPRE, exista la cuenta, este bloqueada o no: el tiempo de
       respuesta no dice cual de los casos fue.
    3. Cuenta bloqueada: nada se cuenta ni se alarga, aunque la clave sea la buena.
    4. Fallo de una cuenta que existe: contador atomico en la base. Cuenta inexistente:
       solo cuenta contra la IP.
    5. Acierto: contador a cero.
    """
    ahora = ahora or datetime.now(UTC)
    if limitador.bloqueada(ip, ahora):
        auditoria.warning("login_rechazado_por_ip ip=%s agente=%s", ip, _nombre_auditable(agente_id))
        return None

    agente = await repo.obtener(agente_id)
    credencial = agente.get("credencial") if agente else None
    correcta = contrasenas.verificar(contrasena, credencial)

    if agente is None:
        limitador.registrar_fallo(ip, ahora)
        auditoria.warning(
            "login_fallido ip=%s agente=%s motivo=cuenta_inexistente",
            ip, _nombre_auditable(agente_id),
        )
        return None

    if esta_bloqueada(agente, ahora):
        limitador.registrar_fallo(ip, ahora)
        auditoria.warning(
            "login_en_cuenta_bloqueada ip=%s agente=%s bloqueado_hasta=%s",
            ip, agente_id, agente["bloqueado_hasta"].isoformat(),
        )
        return None

    if bloqueo_vencido(agente, ahora):
        auditoria.info("desbloqueo agente=%s motivo=vencio_el_plazo", agente_id)

    if correcta and agente.get("activo", True):
        if agente.get("intentos_fallidos") or "bloqueado_hasta" in agente:
            await repo.reiniciar_intentos(agente_id)
        return agente

    limitador.registrar_fallo(ip, ahora)
    resultado = await repo.registrar_fallo(
        agente_id, maximo=maximo, bloqueo_minutos=bloqueo_minutos, ahora=ahora
    )
    intentos = resultado.intentos_fallidos if resultado else 0
    auditoria.warning(
        "login_fallido ip=%s agente=%s intentos=%d motivo=%s",
        ip, agente_id, intentos, "clave_incorrecta" if not correcta else "cuenta_inactiva",
    )
    if resultado is not None and resultado.bloqueo_nuevo:
        auditoria.warning(
            "bloqueo agente=%s ip=%s intentos=%d bloqueado_hasta=%s",
            agente_id, ip, intentos,
            resultado.bloqueado_hasta.isoformat() if resultado.bloqueado_hasta else "",
        )
    return None
