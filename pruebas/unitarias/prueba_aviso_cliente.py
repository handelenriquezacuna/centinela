"""T-82 · El aviso al cliente probado sin base: texto, enmascarado, token, vigencia y el paso de D2.

- El SMS no lleva datos sensibles (ni nombre, ni cuenta completa, ni destino).
- El telefono y el destino se enmascaran; el token es aleatorio y solo se guarda su hash.
- Un enlace vence, y despues de responder deja de admitir respuesta.
- Sin telefono o sin `acepta_sms: true` no se envia nada; un reproceso no manda otro SMS.
- El limite de abuso corta a quien prueba enlaces al azar.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.api.cliente import estado_del_caso
from app.api.limitador import LimitadorIntentos
from app.deteccion.aviso_cliente import (
    AvisoCliente,
    EnviadorSmsSimulado,
    ProtocoloRepoAviso,
    ResultadoAviso,
    notificacion_sms_id_para,
    puede_recibir_sms,
    texto_sms,
)
from app.modelos import Alerta
from app.nucleo.config import AjustesAvisoCliente, AjustesNotificaciones
from app.nucleo.enmascarar import cuenta_destino, telefono, terminada_en
from app.seguridad.enlaces import (
    EstadoEnlace,
    estado_del_enlace,
    hash_de,
    nuevo_token,
    tiene_forma_de_token,
    vence_en,
)
from pruebas.dobles.alertas_d2 import alerta_documento

AHORA = datetime(2026, 9, 25, 4, 47, 5, tzinfo=UTC)
NOTIFICACIONES = AjustesNotificaciones(espera_inicial_segundos=0.01, espera_maxima_segundos=0.02)
AJUSTES = AjustesAvisoCliente()


class RepoAvisoFalso:
    """Cumple `ProtocoloRepoAviso` en memoria y anota las notificaciones que escribe."""

    def __init__(self, contacto: Mapping[str, Any] | None) -> None:
        self.contacto = contacto
        self.notificaciones: dict[str, dict[str, Any]] = {}

    async def contacto_de_alerta(self, transaccion_id: str) -> Mapping[str, Any] | None:
        return self.contacto

    async def crear_pendiente(self, documento: Mapping[str, Any]) -> bool:
        if documento["_id"] in self.notificaciones:
            return False
        self.notificaciones[documento["_id"]] = dict(documento)
        return True

    async def leer_notificacion(self, notificacion_id: str) -> Mapping[str, Any] | None:
        return self.notificaciones.get(notificacion_id)

    async def rotar_enlace(self, notificacion_id: str, *, enlace_hash: str, enlace_vence: datetime) -> None:
        self.notificaciones[notificacion_id].update(enlace_hash=enlace_hash, enlace_vence=enlace_vence)

    async def registrar_intento(
        self,
        notificacion_id: str,
        *,
        estado_envio: str,
        intentos: int,
        fecha: datetime,
        detalle_error: str | None,
        texto: str | None,
    ) -> None:
        nota = self.notificaciones[notificacion_id]
        nota.update(estado_envio=estado_envio, intentos=intentos, fecha=fecha)
        if texto is not None:
            nota["texto"] = texto


class EnviadorQueFalla:
    def __init__(self) -> None:
        self.intentos = 0

    async def enviar(self, telefono: str, texto: str) -> str | None:
        self.intentos += 1
        raise ConnectionError("pasarela caida")


async def _sin_esperar(_: float) -> None:
    return None


def _aviso(repo: RepoAvisoFalso, **extra: Any) -> AvisoCliente:
    return AvisoCliente(
        repo,
        ajustes=AJUSTES,
        notificaciones=NOTIFICACIONES,
        reloj=lambda: AHORA,
        esperar=_sin_esperar,
        **extra,
    )


CONTACTO = {"telefono": "8712-4455", "acepta_sms": True}


def prueba_el_doble_cumple_el_protocolo() -> None:
    assert isinstance(RepoAvisoFalso(None), ProtocoloRepoAviso)


# ---------------------------------------------------------------------------
# Texto y enmascarado
# ---------------------------------------------------------------------------


def prueba_el_sms_dice_lo_necesario_y_nada_sensible() -> None:
    alerta = Alerta.model_validate(alerta_documento(7))
    texto = texto_sms(alerta, "http://127.0.0.1:8000/c/abc")

    assert texto == (
        "Banco: detectamos una transferencia SINPE de ₡280.000 desde tu cuenta terminada en "
        "4455. ¿Fuiste tú? Responde aquí: http://127.0.0.1:8000/c/abc"
    )
    assert alerta.cliente_nombre not in texto and "Maria" not in texto
    assert alerta.cuenta_origen not in texto  # 8712-4455 completa
    assert alerta.cuenta_destino not in texto
    assert alerta.alerta_id not in texto and alerta.transaccion_id not in texto


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [("8712-4455", "8***-**55"), ("6210-3387", "6***-**87"), (None, None), ("basura", "****-****")],
)
def prueba_el_telefono_se_enmascara(valor: str | None, esperado: str | None) -> None:
    assert telefono(valor) == esperado


def prueba_el_destino_y_la_cuenta_se_enmascaran() -> None:
    assert cuenta_destino("6033-9001") == "6033-****"
    assert cuenta_destino("x") == "****-****"
    assert terminada_en("8712-4455") == "4455"
    assert terminada_en("x") == "****"


# ---------------------------------------------------------------------------
# Token, hash, vencimiento, un solo uso
# ---------------------------------------------------------------------------


def prueba_el_token_es_aleatorio_y_el_hash_es_estable_y_no_lo_revela() -> None:
    tokens = {nuevo_token() for _ in range(200)}
    assert len(tokens) == 200
    token = next(iter(tokens))
    assert tiene_forma_de_token(token)
    assert len(token) >= 43
    assert hash_de(token) == hash_de(token)
    assert len(hash_de(token)) == 64 and token not in hash_de(token)
    assert hash_de(token) != hash_de(token + "x")


@pytest.mark.parametrize("basura", ["", "corto", "a" * 31, "a" * 129, "con espacio " * 4, "../" * 20, "<script>" * 6])
def prueba_un_texto_que_no_puede_ser_token_no_se_busca(basura: str) -> None:
    assert not tiene_forma_de_token(basura)


def prueba_el_enlace_vence_a_los_minutos_configurados() -> None:
    vence = vence_en(AHORA, 15)
    assert vence - AHORA == timedelta(minutes=15)
    kwargs = {"vence": vence, "usado_en": None, "seguimiento_horas": 24}
    assert estado_del_enlace(ahora=AHORA + timedelta(minutes=14, seconds=59), **kwargs) is EstadoEnlace.VIGENTE
    assert estado_del_enlace(ahora=AHORA + timedelta(minutes=15), **kwargs) is EstadoEnlace.NO_VALIDO


def prueba_responder_gasta_el_enlace_pero_deja_ver_el_estado_durante_el_seguimiento() -> None:
    vence = vence_en(AHORA, 15)
    usado = AHORA + timedelta(minutes=2)
    kwargs = {"vence": vence, "usado_en": usado, "seguimiento_horas": 24}
    # Ya vencio para responder, pero respondio a tiempo: se puede ver (solo lectura).
    assert estado_del_enlace(ahora=AHORA + timedelta(hours=3), **kwargs) is EstadoEnlace.RESPONDIDO
    assert estado_del_enlace(ahora=usado + timedelta(hours=24), **kwargs) is EstadoEnlace.NO_VALIDO


def prueba_los_estados_del_caso_son_los_del_producto() -> None:
    def titulo(estado: str, respondio: bool = True) -> str | None:
        caso = estado_del_caso(estado, tiene_respuesta=respondio, telefono_ayuda="800-000-0000")
        return caso.titulo if caso else None

    assert titulo("nueva") == "Recibimos tu respuesta"
    assert titulo("en_revision") == "Un agente revisa tu caso"
    assert titulo("confirmada") == "Confirmamos el fraude: llama al 800-000-0000"
    assert titulo("descartada") == "Cerramos el caso: no hubo fraude"
    assert titulo("nueva", respondio=False) is None


# ---------------------------------------------------------------------------
# El paso de D2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_una_alerta_critica_con_consentimiento_deja_un_sms_con_solo_el_hash() -> None:
    repo = RepoAvisoFalso(CONTACTO)
    resultado = await _aviso(repo, enviador=EnviadorSmsSimulado()).procesar(alerta_documento(7))

    assert resultado is ResultadoAviso.ENVIADO
    nota = repo.notificaciones[notificacion_sms_id_para("ALR-000007")]
    assert nota["_id"] == "NOT-000007-SMS" and nota["canal"] == "sms"
    assert nota["estado_envio"] == "enviado" and nota["intentos"] == 1
    assert nota["destinatarios"] == ["8***-**55"]  # el telefono completo no se guarda
    assert nota["enlace_vence"] - AHORA == timedelta(minutes=15)
    # El texto SI trae el enlace (bandeja del demo); el hash guardado corresponde a ese token.
    token = nota["texto"].rsplit("/c/", 1)[1]
    assert nota["enlace_hash"] == hash_de(token)
    assert token not in {v for k, v in nota.items() if k != "texto" and isinstance(v, str)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contacto",
    [
        None,
        {"acepta_sms": True},
        {"telefono": "6210-3387", "acepta_sms": False},
        {"telefono": "6210-3387"},
    ],
)
async def prueba_sin_telefono_o_sin_consentimiento_no_se_envia(contacto: Mapping[str, Any] | None) -> None:
    repo = RepoAvisoFalso(contacto)
    assert puede_recibir_sms(contacto) is False

    assert await _aviso(repo).procesar(alerta_documento(1)) is ResultadoAviso.SIN_CONSENTIMIENTO
    assert repo.notificaciones == {}


@pytest.mark.asyncio
async def prueba_solo_las_criticas_se_avisan() -> None:
    from app.modelos import Severidad

    repo = RepoAvisoFalso(CONTACTO)
    alta = alerta_documento(2, severidad=Severidad.ALTA, puntaje=60)
    assert await _aviso(repo).procesar(alta) is ResultadoAviso.IGNORADO
    assert repo.notificaciones == {}


@pytest.mark.asyncio
async def prueba_reprocesar_no_manda_otro_sms() -> None:
    repo = RepoAvisoFalso(CONTACTO)
    aviso = _aviso(repo)
    await aviso.procesar(alerta_documento(3))
    primero = dict(repo.notificaciones["NOT-000003-SMS"])

    assert await aviso.procesar(alerta_documento(3)) is ResultadoAviso.YA_PROCESADO
    assert repo.notificaciones["NOT-000003-SMS"] == primero


@pytest.mark.asyncio
async def prueba_una_notificacion_pendiente_se_retoma_con_un_token_nuevo() -> None:
    repo = RepoAvisoFalso(CONTACTO)
    tokens = iter(["a" * 43, "b" * 43])
    aviso = _aviso(repo, generar_token=lambda: next(tokens))
    # D2 murio despues de crear la pendiente: queda con el hash del primer token.
    from app.deteccion.aviso_cliente import documento_pendiente

    alerta = Alerta.model_validate(alerta_documento(4))
    await repo.crear_pendiente(
        documento_pendiente(alerta, telefono="8712-4455", enlace_hash=hash_de("a" * 43), vence=AHORA, ahora=AHORA)
    )
    next(tokens)  # el primer token se perdio con el proceso

    assert await aviso.procesar(alerta_documento(4)) is ResultadoAviso.ENVIADO
    nota = repo.notificaciones["NOT-000004-SMS"]
    assert nota["enlace_hash"] == hash_de("b" * 43) and nota["texto"].endswith("/c/" + "b" * 43)


@pytest.mark.asyncio
async def prueba_un_proveedor_que_falla_agota_los_reintentos_y_queda_fallido() -> None:
    repo = RepoAvisoFalso(CONTACTO)
    enviador = EnviadorQueFalla()

    resultado = await _aviso(repo, enviador=enviador).procesar(alerta_documento(5))

    assert resultado is ResultadoAviso.FALLIDO
    assert enviador.intentos == NOTIFICACIONES.reintentos_envio
    nota = repo.notificaciones["NOT-000005-SMS"]
    assert nota["estado_envio"] == "fallido" and "texto" not in nota


# ---------------------------------------------------------------------------
# Limite de abuso
# ---------------------------------------------------------------------------


def prueba_el_limite_corta_a_la_ip_que_prueba_enlaces_y_se_libera_con_la_ventana() -> None:
    reloj = [0.0]
    limite = LimitadorIntentos(
        invalidos_por_ip=3, respuestas_por_enlace=2, ventana_segundos=60, reloj=lambda: reloj[0]
    )
    for _ in range(3):
        assert not limite.ip_bloqueada("1.1.1.1")
        limite.anotar_invalido("1.1.1.1")
    assert limite.ip_bloqueada("1.1.1.1") and not limite.ip_bloqueada("2.2.2.2")
    assert limite.segundos_de_espera("1.1.1.1") >= 1
    reloj[0] = 61
    assert not limite.ip_bloqueada("1.1.1.1")


def prueba_el_limite_de_respuestas_es_por_token_e_ip() -> None:
    limite = LimitadorIntentos(invalidos_por_ip=10, respuestas_por_enlace=2, ventana_segundos=60)
    assert limite.intento_de_respuesta("h", "ip") and limite.intento_de_respuesta("h", "ip")
    assert not limite.intento_de_respuesta("h", "ip")
    assert limite.intento_de_respuesta("h", "otra") and limite.intento_de_respuesta("otro", "ip")
