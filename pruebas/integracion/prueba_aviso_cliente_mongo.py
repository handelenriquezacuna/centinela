"""T-82 · El aviso al cliente contra MongoDB de verdad.

Base propia (`antifraude_pruebas_d2`, con los validadores `$jsonSchema` reales de la semilla):

- una alerta CRITICA de un cliente con consentimiento genera el SMS; sin consentimiento o sin
  telefono, no;
- en la base queda SOLO el hash del token (el texto lleva el enlace: es la bandeja del demo);
- responder "No fui yo" deja la entrada en el historial (sin agente), la marca en la alerta y la
  notificacion gastada, sin mover el estado; un segundo intento y un enlace vencido no pasan;
- reprocesar el evento no duplica el SMS;
- la cola sube las alertas "No fui yo" al tope, paginada sin huecos ni repetidos, y el caso por
  cuenta destino que las trae va primero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.deteccion.aviso_cliente import AvisoCliente
from app.modelos import Severidad
from app.repos.alertas import RepoAlertas
from app.repos.aviso_cliente import EnlaceNoDisponible, RepoAviso, RepoRespuestaCliente
from app.repos.carga_demo import a_entero_64
from app.repos.casos_destino import RepoCasosDestino
from app.seguridad.enlaces import hash_de
from pruebas.dobles.alertas_d2 import alerta_documento
from pruebas.dobles.servidor_smtp import ServidorSmtp
from pruebas.integracion.apoyo_d2 import (
    ajustes_de_prueba,
    d2_corriendo,
    insertar,
    token_en,
)

pytestmark = pytest.mark.mongo


async def _sembrar_clientes(base: Any) -> None:
    """CLI-001 acepta SMS; CLI-002 tiene telefono y no acepta; CLI-003 no tiene telefono."""
    await base["clientes"].insert_many(
        [
            {"_id": "CLI-001", "nombre": "Maria", "cedula": "1-1111-1111", "telefono": "8712-4455", "acepta_sms": True},
            {"_id": "CLI-002", "nombre": "Luis", "cedula": "2-2222-2222", "telefono": "6210-3387", "acepta_sms": False},
            {"_id": "CLI-003", "nombre": "Ana", "cedula": "3-3333-3333"},
        ]
    )
    await base["cuentas"].insert_many(
        [{"_id": f"CTA-00{n}", "cuenta_sinpe": f"000{n}-0000", "cliente_id": f"CLI-00{n}"} for n in (1, 2, 3)]
    )
    await base["transacciones"].insert_many(
        [{"_id": f"TXN-00000{n}", "cuenta_origen": f"CTA-00{n}"} for n in (1, 2, 3)]
    )


def _token_del_texto(nota: dict[str, Any]) -> str:
    return str(nota["texto"]).rsplit("/c/", 1)[1]


@pytest.mark.asyncio
async def prueba_critica_con_consentimiento_genera_el_sms_y_sin_consentimiento_no(base_d2: Any) -> None:
    await _sembrar_clientes(base_d2)
    from app.nucleo.config import AjustesAvisoCliente

    with ServidorSmtp() as smtp:
        ajustes = ajustes_de_prueba(smtp.puerto)
        aviso = AvisoCliente(RepoAviso(base_d2), ajustes=AjustesAvisoCliente(), notificaciones=ajustes)
        async with d2_corriendo(base_d2, ajustes, aviso=aviso):
            await insertar(base_d2, 1, 2, 3)
            await token_en(base_d2, "TXN-000003")

    sms = await base_d2["notificaciones"].find({"canal": "sms"}).to_list(None)
    assert [n["alerta_id"] for n in sms] == ["ALR-000001"]  # CLI-002 no acepta, CLI-003 sin telefono
    nota = sms[0]
    assert nota["_id"] == "NOT-000001-SMS" and nota["estado_envio"] == "enviado"
    assert nota["destinatarios"] == ["8***-**55"]
    assert "8712-4455" not in str(nota)
    token = _token_del_texto(nota)
    assert nota["enlace_hash"] == hash_de(token)
    assert nota["enlace_vence"] - nota["fecha"] <= timedelta(minutes=15, seconds=5)
    assert token not in {v for k, v in nota.items() if k != "texto" and isinstance(v, str)}
    # El correo al supervisor sigue saliendo para las tres.
    assert await base_d2["notificaciones"].count_documents({"canal": "correo"}) == 3


@pytest.mark.asyncio
async def prueba_reprocesar_el_evento_no_duplica_el_sms(base_d2: Any) -> None:
    from app.nucleo.config import AjustesAvisoCliente

    await _sembrar_clientes(base_d2)
    aviso = AvisoCliente(RepoAviso(base_d2), ajustes=AjustesAvisoCliente(), notificaciones=ajustes_de_prueba(1025))
    documento = a_entero_64(alerta_documento(1))

    await aviso.procesar(documento)
    antes = await base_d2["notificaciones"].find_one({"canal": "sms"})
    await aviso.procesar(documento)
    await aviso.procesar(documento)

    assert await base_d2["notificaciones"].count_documents({"canal": "sms"}) == 1
    assert await base_d2["notificaciones"].find_one({"canal": "sms"}) == antes


@pytest.mark.asyncio
async def prueba_no_fui_yo_deja_historial_marca_y_gasta_el_enlace_sin_cerrar_la_alerta(base_d2: Any) -> None:
    from app.nucleo.config import AjustesAvisoCliente

    await _sembrar_clientes(base_d2)
    await base_d2["alertas"].insert_one(a_entero_64(alerta_documento(1)))
    aviso = AvisoCliente(RepoAviso(base_d2), ajustes=AjustesAvisoCliente(), notificaciones=ajustes_de_prueba(1025))
    await aviso.procesar(alerta_documento(1))
    nota = await base_d2["notificaciones"].find_one({"canal": "sms"})
    enlace_hash = nota["enlace_hash"]
    repo = RepoRespuestaCliente(base_d2)
    ahora = datetime.now(UTC)

    await repo.registrar_respuesta(enlace_hash, "no_fui_yo", ahora)

    alerta = await base_d2["alertas"].find_one({"_id": "ALR-000001"})
    assert alerta["estado"] == "nueva" and "agente_id" not in alerta  # decide el agente
    assert alerta["respuesta_cliente"]["valor"] == "no_fui_yo"
    ultima = alerta["historial"][-1]
    assert ultima["accion"] == "respuesta_cliente" and ultima["agente_id"] is None
    assert len(alerta["historial"]) == 2  # la creacion y la respuesta: se AGREGA, no se reescribe
    gastada = await base_d2["notificaciones"].find_one({"_id": nota["_id"]})
    assert gastada["enlace_usado_en"] is not None
    assert gastada["respuesta_cliente"]["valor"] == "no_fui_yo"

    with pytest.raises(EnlaceNoDisponible):  # un solo uso
        await repo.registrar_respuesta(enlace_hash, "si_fui_yo", ahora)
    assert (await base_d2["alertas"].find_one({"_id": "ALR-000001"}))["respuesta_cliente"]["valor"] == "no_fui_yo"


@pytest.mark.asyncio
async def prueba_un_enlace_vencido_no_admite_respuesta_y_no_toca_la_alerta(base_d2: Any) -> None:
    await base_d2["alertas"].insert_one(a_entero_64(alerta_documento(1)))
    ahora = datetime.now(UTC)
    await base_d2["notificaciones"].insert_one(
        {
            "_id": "NOT-000001-SMS",
            "alerta_id": "ALR-000001",
            "canal": "sms",
            "estado_envio": "enviado",
            "fecha": ahora - timedelta(minutes=30),
            "enlace_hash": hash_de("v" * 43),
            "enlace_vence": ahora - timedelta(minutes=15),
            "esquema_version": 1,
        }
    )

    with pytest.raises(EnlaceNoDisponible):
        await RepoRespuestaCliente(base_d2).registrar_respuesta(hash_de("v" * 43), "no_fui_yo", ahora)

    alerta = await base_d2["alertas"].find_one({"_id": "ALR-000001"})
    assert "respuesta_cliente" not in alerta and len(alerta["historial"]) == 1


async def _alerta_con_respuesta(base: Any, numero: int, puntaje: int, severidad: Severidad, valor: str | None) -> None:
    documento = alerta_documento(numero, puntaje=puntaje, severidad=severidad)
    documento["cuenta_destino"] = f"7000-{numero:04d}" if numero != 4 else "7000-0002"
    await base["alertas"].insert_one(a_entero_64(documento))
    if valor is not None:
        enlace = hash_de(f"{numero:0>43}")
        await base["notificaciones"].insert_one(
            {
                "_id": f"NOT-{numero:06d}-SMS",
                "alerta_id": f"ALR-{numero:06d}",
                "canal": "sms",
                "estado_envio": "enviado",
                "fecha": datetime.now(UTC),
                "enlace_hash": enlace,
                "enlace_vence": datetime.now(UTC) + timedelta(minutes=10),
                "esquema_version": 1,
            }
        )
        await RepoRespuestaCliente(base).registrar_respuesta(enlace, valor, datetime.now(UTC))


@pytest.mark.asyncio
async def prueba_la_cola_sube_las_no_fui_yo_y_pagina_sin_huecos_ni_repetidos(base_d2: Any) -> None:
    # Puntajes: 1=90, 2=80, 3=70, 4=60 (no_fui_yo), 5=50, 6=40 (no_fui_yo), 7=30 (si_fui_yo).
    plan = [(1, 90, None), (2, 80, None), (3, 70, None), (4, 60, "no_fui_yo"), (5, 50, None), (6, 40, "no_fui_yo"), (7, 30, "si_fui_yo")]
    for numero, puntaje, valor in plan:
        await _alerta_con_respuesta(base_d2, numero, puntaje, Severidad.ALTA, valor)
    esperado = ["ALR-000004", "ALR-000006", "ALR-000001", "ALR-000002", "ALR-000003", "ALR-000005", "ALR-000007"]
    repo = RepoAlertas(base_d2)

    pagina, total = await repo.listar(limite=50)
    assert [a["_id"] for a in pagina] == esperado and total == 7

    for tamano in (1, 2, 3):  # cualquier tamano de pagina da el mismo orden, sin huecos ni repetidos
        vistas: list[str] = []
        for desplazamiento in range(0, 7, tamano):
            parte, _ = await repo.listar(limite=tamano, desplazamiento=desplazamiento)
            vistas += [a["_id"] for a in parte]
        assert vistas == esperado, f"limite {tamano}"

    # Una alerta CERRADA con "No fui yo" ya no sube; y con filtro por estado cerrado no hay prioridad.
    await base_d2["alertas"].update_one({"_id": "ALR-000006"}, {"$set": {"estado": "confirmada"}})
    pagina, _ = await repo.listar(limite=50)
    assert [a["_id"] for a in pagina][:2] == ["ALR-000004", "ALR-000001"]


@pytest.mark.asyncio
async def prueba_el_caso_con_no_fui_yo_va_primero_y_su_alerta_encabeza_el_caso(base_d2: Any) -> None:
    # Destinos: 7000-0001 (ALR-1, 90), 7000-0002 (ALR-2 80 y ALR-4 60 con "no fui yo"), 7000-0003 (ALR-3, 70).
    for numero, puntaje, valor in [(1, 90, None), (2, 80, None), (3, 70, None), (4, 60, "no_fui_yo")]:
        await _alerta_con_respuesta(base_d2, numero, puntaje, Severidad.ALTA, valor)

    casos, total = await RepoCasosDestino(base_d2).listar(limite=10)

    assert total == 3
    assert [c.cuenta_destino for c in casos] == ["7000-0002", "7000-0001", "7000-0003"]
    primero = casos[0]
    assert primero.no_fui_yo == 1
    assert [a["_id"] for a in primero.alertas] == ["ALR-000004", "ALR-000002"]  # la marcada, primero
    assert primero.alertas[0]["respuesta_cliente"]["valor"] == "no_fui_yo"
    assert "_no_fui_yo" not in primero.alertas[0]
