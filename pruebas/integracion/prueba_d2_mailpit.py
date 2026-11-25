"""H-11 · El correo llega de verdad a un Mailpit y se lee por su API HTTP.

Mailpit es el servidor SMTP de pruebas del proyecto (`infra/docker-compose.yml`, servicio
`mailpit`). Aqui se usa uno aparte, el de las pruebas, para no mezclar con el de trabajo:

    docker run -d --rm --name t77-mailpit -p 127.0.0.1:1125:1025 -p 127.0.0.1:8125:8025 \\
        axllent/mailpit:v1.29.1

El CI lo levanta igual (`.github/workflows/ci.yml`). Los puertos se pueden mover con
`CENTINELA_PRUEBAS_MAILPIT_SMTP` (por omision 1125) y `CENTINELA_PRUEBAS_MAILPIT_API` (por
omision `http://127.0.0.1:8125`). Sin Mailpit la prueba se salta; con `CENTINELA_EXIGIR_MONGO=1`
(el CI) es una falla, como la falta de Mongo.

La API que se consulta es la de Mailpit: `GET /api/v1/messages`, `GET /api/v1/message/{id}`
y `DELETE /api/v1/messages`.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from typing import Any

import pytest
import pytest_asyncio

from app.deteccion.d2 import NOMBRE_D2
from app.modelos import Severidad
from pruebas.conftest import saltar_o_fallar
from pruebas.integracion.apoyo_d2 import (
    ajustes_de_prueba,
    d2_corriendo,
    insertar,
    token_en,
)

pytestmark = pytest.mark.mongo

PUERTO_SMTP = int(os.environ.get("CENTINELA_PRUEBAS_MAILPIT_SMTP", "1125"))
API = os.environ.get("CENTINELA_PRUEBAS_MAILPIT_API", "http://127.0.0.1:8125").rstrip("/")


def _http(metodo: str, ruta: str) -> Any:
    peticion = urllib.request.Request(f"{API}{ruta}", method=metodo)
    with urllib.request.urlopen(peticion, timeout=5) as respuesta:
        cuerpo = respuesta.read()
    try:
        return json.loads(cuerpo)
    except ValueError:  # DELETE responde "ok" en texto plano
        return cuerpo.decode("utf-8")


async def _mailpit(metodo: str, ruta: str) -> Any:
    return await asyncio.to_thread(_http, metodo, ruta)


@pytest_asyncio.fixture
async def mailpit() -> None:
    """Mailpit vacio, o salto/falla si no esta."""
    try:
        await _mailpit("GET", "/api/v1/info")
    except OSError:
        saltar_o_fallar(
            f"Mailpit no responde en {API}. Levantarlo con: docker run -d --rm --name "
            "t77-mailpit -p 127.0.0.1:1125:1025 -p 127.0.0.1:8125:8025 axllent/mailpit:v1.29.1"
        )
    await _mailpit("DELETE", "/api/v1/messages")


async def _mensajes() -> list[dict[str, Any]]:
    return (await _mailpit("GET", "/api/v1/messages"))["messages"]


@pytest.mark.asyncio
async def prueba_mailpit_recibe_el_correo_de_una_alerta_critica(mailpit: None, base_d2: Any) -> None:
    async with d2_corriendo(base_d2, ajustes_de_prueba(PUERTO_SMTP, smtp_host="127.0.0.1")):
        await insertar(base_d2, 1, severidad=Severidad.ALTA)
        await insertar(base_d2, 2)
        await token_en(base_d2, "TXN-000002")

    recibidos = await _mensajes()
    assert len(recibidos) == 1, "solo la CRITICA produce correo"
    correo = recibidos[0]
    assert correo["Subject"] == "[Centinela] Alerta crítica ALR-000002: ₡280.000 de Maria Rodriguez Vargas"
    assert [destinatario["Address"] for destinatario in correo["To"]] == ["guardia@centinela.local"]
    assert correo["From"]["Address"] == "centinela@centinela.local"

    completo = await _mailpit("GET", f"/api/v1/message/{correo['ID']}")
    assert "Alerta: ALR-000002" in completo["Text"]
    assert "Monto: ₡280.000" in completo["Text"]
    assert "Severidad: Crítica (puntaje 75 de 100)" in completo["Text"]
    assert "R-01 v1 (35 puntos)" in completo["Text"]
    assert "http://127.0.0.1:8000/detalle/ALR-000002" in completo["Text"]
    assert "al menos una vez" in completo["Text"]

    nota = await base_d2["notificaciones"].find_one({"alerta_id": "ALR-000002"})
    assert nota["estado_envio"] == "enviado"
    assert nota["asunto"] == correo["Subject"]


@pytest.mark.asyncio
async def prueba_reprocesar_no_manda_un_segundo_correo_a_mailpit(mailpit: None, base_d2: Any) -> None:
    ajustes = ajustes_de_prueba(PUERTO_SMTP, smtp_host="127.0.0.1")
    async with d2_corriendo(base_d2, ajustes):
        token_inicial = await base_d2["deteccion_estado"].find_one({"_id": NOMBRE_D2})
        await insertar(base_d2, 1)
        await token_en(base_d2, "TXN-000001")
    assert len(await _mensajes()) == 1

    # Murio entre el correo y el token: el token quedo atras y se vuelve a entregar el evento.
    await base_d2["deteccion_estado"].replace_one({"_id": NOMBRE_D2}, token_inicial)
    async with d2_corriendo(base_d2, ajustes):
        await token_en(base_d2, "TXN-000001")

    assert len(await _mensajes()) == 1, "el reproceso no manda un segundo correo"
    assert await base_d2["notificaciones"].count_documents({}) == 1
