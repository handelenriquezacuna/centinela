"""Alertas y doble del repositorio de D2 (T-77).

`alerta_documento` arma el documento v2 que escribe D1 (reglas embebidas con version y
explicacion, politica usada). `RepoNotificacionesFalso` cumple `ProtocoloRepoNotificaciones`
de forma estructural y anota cada escritura en `bitacora`, en el orden en que D2 la pidio:
con eso se prueba el contrato "primero la notificacion, despues el token" sin base.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.modelos import (
    ESQUEMA_ALERTA_V2,
    AccionHistorial,
    Alerta,
    EstadoAlerta,
    PoliticaAplicada,
    ReglaAplicada,
    Severidad,
)
from app.repos.flujo_alertas import TokenDeReanudacionInvalido
from app.repos.notificaciones import EventoAlerta

CREADA = datetime(2026, 9, 25, 4, 47, 0, tzinfo=UTC)  # 22:47 en Costa Rica


def alerta_documento(
    numero: int = 1,
    *,
    severidad: Severidad = Severidad.CRITICA,
    puntaje: int = 75,
    monto_crc: int = 280_000,
    cliente: str = "Maria Rodriguez Vargas",
) -> dict[str, Any]:
    """Una alerta v2 como la deja D1: `ALR-00000N` de `TXN-00000N`."""
    return Alerta(
        _id=f"ALR-{numero:06d}",
        esquema_version=ESQUEMA_ALERTA_V2,
        transaccion_id=f"TXN-{numero:06d}",
        reglas_disparadas=[
            ReglaAplicada(
                regla_id="REG-001",
                codigo="R-01",
                version=1,
                peso=35,
                explicacion="Monto de 280000 colones, superior a 10 veces el promedio del cliente",
            ),
            ReglaAplicada(
                regla_id="REG-003",
                codigo="R-05",
                version=2,
                peso=15,
                explicacion="Transferencia a las 22:47, fuera del horario habitual",
            ),
        ],
        politica=PoliticaAplicada(politica_id="POL-001", version=1),
        puntaje=puntaje,
        severidad=severidad,
        estado=EstadoAlerta.NUEVA,
        fecha_creacion=CREADA,
        historial=[AccionHistorial(accion=EstadoAlerta.NUEVA, fecha=CREADA, nota="creada")],
        cuenta_origen="8712-4455",
        cuenta_destino="6033-9001",
        monto_crc=monto_crc,
        cliente_nombre=cliente,
    ).a_documento()


def token(numero: int) -> dict[str, str]:
    return {"_data": f"82{numero:06X}"}


def vuelta(numero: int, **cambios: Any) -> EventoAlerta:
    """La vuelta del change stream que trae `alerta_documento(numero)`."""
    return EventoAlerta(token=token(numero), documento=alerta_documento(numero, **cambios))


class RepoNotificacionesFalso:
    """Entrega vueltas y anota cada escritura en `bitacora`.

    - `vueltas`: lo que entrega la apertura (al reanudar, solo lo posterior al token).
      Agotadas, se queda esperando para siempre, como un cursor sin trafico.
    - `existentes`: notificaciones ya guardadas, por `_id` (el reproceso).
    - `fallos_al_crear`: cuantas veces falla `crear_pendiente` antes de funcionar.
    - `rechazar_token`: la primera apertura con token falla como `ChangeStreamHistoryLost`.
    """

    def __init__(
        self,
        vueltas: Sequence[EventoAlerta],
        *,
        token_guardado: Mapping[str, Any] | None = None,
        existentes: Mapping[str, dict[str, Any]] | None = None,
        fallos_al_crear: int = 0,
        rechazar_token: bool = False,
        descarte_roto: bool = False,
    ) -> None:
        self._vueltas = list(vueltas)
        self.token_guardado = token_guardado
        self.notificaciones: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in (existentes or {}).items()
        }
        self._fallos_al_crear = fallos_al_crear
        self._rechazar_token = rechazar_token
        self._descarte_roto = descarte_roto
        self.bitacora: list[str] = []
        self.aperturas: list[Mapping[str, Any] | None] = []
        self.descartes: list[dict[str, Any]] = []
        self.agotado = asyncio.Event()
        self.entregadas = 0

    async def esperar_procesado(self, supervisor: Any, *, nombre: str = "d2_notificacion") -> None:
        await asyncio.wait_for(self.agotado.wait(), timeout=5)

        async def terminado() -> None:
            while supervisor.informe().tarea(nombre).eventos_procesados < self.entregadas:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(terminado(), timeout=5)

    async def observar_alertas_criticas(
        self, *, desde_token: Mapping[str, Any] | None, espera_ms: int
    ) -> AsyncIterator[EventoAlerta]:
        self.aperturas.append(desde_token)
        if desde_token is not None and self._rechazar_token:
            self._rechazar_token = False
            raise TokenDeReanudacionInvalido(
                "el token de reanudacion de alertas ya no sirve (ChangeStreamHistoryLost)"
            )
        pendientes = self._vueltas
        if desde_token is not None:
            for indice, evento in enumerate(self._vueltas):
                if evento.token == desde_token:
                    pendientes = self._vueltas[indice + 1 :]
                    break
        for evento in pendientes:
            self.entregadas += 1
            yield evento
        self.agotado.set()
        await asyncio.Event().wait()

    async def crear_pendiente(self, documento: Mapping[str, Any]) -> bool:
        if self._fallos_al_crear > 0:
            self._fallos_al_crear -= 1
            raise ConnectionError("la base no contesta")
        if documento["_id"] in self.notificaciones:
            self.bitacora.append(f"duplicada:{documento['_id']}")
            return False
        self.notificaciones[documento["_id"]] = dict(documento)
        self.bitacora.append(f"pendiente:{documento['_id']}")
        return True

    async def leer_notificacion(self, notificacion_id: str) -> Mapping[str, Any] | None:
        return self.notificaciones.get(notificacion_id)

    async def registrar_intento(
        self,
        notificacion_id: str,
        *,
        estado_envio: str,
        intentos: int,
        fecha: datetime,
        detalle_error: str | None,
    ) -> None:
        documento = self.notificaciones[notificacion_id]
        documento.update(estado_envio=estado_envio, intentos=intentos, fecha=fecha)
        if detalle_error is None:
            documento.pop("detalle_error", None)
        else:
            documento["detalle_error"] = detalle_error
        self.bitacora.append(f"{estado_envio}:{notificacion_id}:{intentos}")

    async def leer_token(self, disparador: str) -> Mapping[str, Any] | None:
        return self.token_guardado

    async def guardar_token(
        self, disparador: str, token: Mapping[str, Any], *, transaccion_id: str | None
    ) -> None:
        self.token_guardado = dict(token)
        self.bitacora.append(f"token:{transaccion_id}")

    async def descartar(
        self, disparador: str, *, transaccion_id: str, motivo: str, intentos: int
    ) -> None:
        if self._descarte_roto:
            raise ConnectionError("el descarte tampoco se puede escribir")
        self.descartes.append(
            {"transaccion_id": transaccion_id, "motivo": motivo, "intentos": intentos}
        )
        self.bitacora.append(f"descarte:{transaccion_id}")
