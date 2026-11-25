"""H-11 · D2 contra MongoDB de verdad: change stream, validadores, indice unico y reproceso.

Necesita el replica set (un change stream no existe sin oplog); se salta solo si no
responde. Corre en una base propia, `antifraude_pruebas_d2`, que se tira al terminar.

Lo que se usa de verdad, sin dobles:

- los validadores `$jsonSchema` de `alertas`, `notificaciones` y `deteccion_estado`,
  copiados de la semilla de las pruebas (los siembra `01-colecciones.js`): si la notificacion
  o el token que escribe D2 no cumplen el validador, la prueba falla;
- el indice unico `(alerta_id, canal)` de `notificaciones`, el mismo de `02-indices.js`:
  es lo que impide el duplicado, no el codigo de D2;
- un servidor SMTP en proceso (`pruebas/dobles/servidor_smtp.py`) con el que `smtplib` habla
  por un socket real, y un puerto cerrado para el fallo.

El correo contra un Mailpit de verdad lo prueba `prueba_d2_mailpit.py`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from pymongo.errors import DuplicateKeyError, WriteError

from app.deteccion.d2 import NOMBRE_D2
from app.main import crear_aplicacion
from app.modelos import Severidad
from app.nucleo.config import obtener_ajustes
from app.repos.carga_demo import a_entero_64
from app.repos.deteccion import RepoDeteccion
from app.repos.notificaciones import RepoLecturaNotificaciones
from pruebas.dobles.alertas_d2 import alerta_documento
from pruebas.dobles.servidor_smtp import ServidorSmtp
from pruebas.integracion.apoyo_d2 import (
    BASE_D2,
    ajustes_de_prueba,
    d2_corriendo,
    esperar,
    insertar,
    puerto_cerrado,
    token_en,
)
from pruebas.sesiones import encabezados_de

pytestmark = pytest.mark.mongo

# ---------------------------------------------------------------------------
# El caso
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_una_alerta_critica_produce_una_notificacion_y_un_correo(base_d2: Any) -> None:
    with ServidorSmtp() as smtp:
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)) as supervisor:
            await insertar(base_d2, 1)
            await token_en(base_d2, "TXN-000001")
            fila = supervisor.informe().tarea(NOMBRE_D2)
        mensajes = smtp.mensajes()

    nota = await base_d2["notificaciones"].find_one({"alerta_id": "ALR-000001"})
    assert nota["_id"] == "NOT-000001"
    assert nota["canal"] == "correo"
    assert nota["estado_envio"] == "enviado"
    assert nota["intentos"] == 1
    assert nota["destinatarios"] == ["guardia@centinela.local"]
    assert nota["esquema_version"] == 1
    assert "detalle_error" not in nota
    assert len(mensajes) == 1
    assert str(mensajes[0]["Subject"]) == (
        "[Centinela] Alerta crítica ALR-000001: ₡280.000 de Maria Rodriguez Vargas"
    )
    assert fila.estado == "corriendo"
    assert fila.ultimo_error is None


@pytest.mark.asyncio
async def prueba_una_alerta_que_no_es_critica_no_produce_nada(base_d2: Any) -> None:
    with ServidorSmtp() as smtp:
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)):
            await insertar(base_d2, 1, severidad=Severidad.ALTA)
            await insertar(base_d2, 2, severidad=Severidad.MEDIA)
            # La critica que viene despues es la prueba de que D2 ya paso por las otras.
            await insertar(base_d2, 3)
            await token_en(base_d2, "TXN-000003")
        mensajes = smtp.mensajes()

    assert [n["alerta_id"] async for n in base_d2["notificaciones"].find({})] == ["ALR-000003"]
    assert [m["X-Centinela-Alerta"] for m in mensajes] == ["ALR-000003"]


@pytest.mark.asyncio
async def prueba_una_rafaga_de_criticas_produce_una_notificacion_por_alerta(base_d2: Any) -> None:
    with ServidorSmtp() as smtp:
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)):
            await insertar(base_d2, *range(1, 11))
            await token_en(base_d2, "TXN-000010")
        recibidos = [m["X-Centinela-Alerta"] for m in smtp.mensajes()]

    assert sorted(recibidos) == [f"ALR-{n:06d}" for n in range(1, 11)]
    assert await base_d2["notificaciones"].count_documents({"estado_envio": "enviado"}) == 10


# ---------------------------------------------------------------------------
# Resiliencia
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_reanuda_desde_el_token_sin_duplicar_ni_perder(base_d2: Any) -> None:
    with ServidorSmtp() as smtp:
        # 1. D2 arriba procesa la primera.
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)):
            await insertar(base_d2, 1)
            await token_en(base_d2, "TXN-000001")
        token_tras_la_primera = await base_d2["deteccion_estado"].find_one({"_id": NOMBRE_D2})

        # 2. D2 abajo: entran alertas que nadie esta mirando.
        await insertar(base_d2, 2, 3, 4)
        assert await base_d2["notificaciones"].count_documents({}) == 1

        # 3. D2 vuelve: reanuda desde el token y no pierde ninguna.
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)):
            await token_en(base_d2, "TXN-000004")
        assert await base_d2["notificaciones"].count_documents({}) == 4
        assert len(smtp.mensajes()) == 4

        # 4. El proceso "murio" entre la notificacion y el token: el token quedo atras. D2
        #    reprocesa ALR-2..4 y el indice unico rechaza cada duplicado: no hay otro
        #    registro ni otro correo.
        antes = {n["_id"]: n["fecha"] async for n in base_d2["notificaciones"].find({})}
        await base_d2["deteccion_estado"].replace_one({"_id": NOMBRE_D2}, token_tras_la_primera)
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)) as supervisor:
            await token_en(base_d2, "TXN-000004")
            fila = supervisor.informe().tarea(NOMBRE_D2)
        mensajes_finales = len(smtp.mensajes())

    despues = {n["_id"]: n["fecha"] async for n in base_d2["notificaciones"].find({})}
    assert despues == antes, "el reproceso no reescribe notificaciones existentes"
    assert mensajes_finales == 4, "ni envia otro correo"
    assert fila.ultimo_error is None, "un reproceso es funcionamiento normal, no un error"
    assert await base_d2["deteccion_estado"].count_documents({"clase": "descarte"}) == 0


@pytest.mark.asyncio
async def prueba_una_pendiente_de_una_caida_se_retoma_con_los_intentos_que_faltaban(base_d2: Any) -> None:
    """D2 murio despues del primer intento de ALR-000002: al volver quedan los que faltan."""
    with ServidorSmtp() as smtp:
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)):
            await insertar(base_d2, 1)
            await token_en(base_d2, "TXN-000001")
        # D2 abajo: entra ALR-000002 y la corrida anterior alcanzo a dejar su notificacion
        # `pendiente` con un intento hecho (tope de la prueba: 2).
        await insertar(base_d2, 2)
        await base_d2["notificaciones"].insert_one(
            {
                "_id": "NOT-000002", "alerta_id": "ALR-000002", "canal": "correo",
                "estado_envio": "pendiente", "intentos": 1,
                "fecha": alerta_documento()["fecha_creacion"],
                "detalle_error": "ConnectionRefusedError: ...", "esquema_version": 1,
            }
        )
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)):
            await token_en(base_d2, "TXN-000002")
        enviados = sorted(m["X-Centinela-Alerta"] for m in smtp.mensajes())

    nota = await base_d2["notificaciones"].find_one({"_id": "NOT-000002"})
    assert nota["estado_envio"] == "enviado"
    assert nota["intentos"] == 2, "retomo en el intento 2, no empezo de cero"
    assert "detalle_error" not in nota
    assert enviados == ["ALR-000001", "ALR-000002"]


@pytest.mark.asyncio
async def prueba_un_token_inservible_resincroniza_y_sigue_notificando(base_d2: Any) -> None:
    await RepoDeteccion(base_d2).guardar_token(
        NOMBRE_D2, {"_data": "82DEADBEEF"}, transaccion_id=None
    )

    with ServidorSmtp() as smtp:
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)) as supervisor:
            error = supervisor.informe().tarea(NOMBRE_D2).ultimo_error
            await insertar(base_d2, 1)
            await token_en(base_d2, "TXN-000001")
        mensajes = smtp.mensajes()

    assert error is not None and "token de reanudacion" in error.mensaje
    assert len(mensajes) == 1
    estado = await base_d2["deteccion_estado"].find_one({"_id": NOMBRE_D2})
    assert estado["token_reanudacion"] != {"_data": "82DEADBEEF"}


@pytest.mark.asyncio
async def prueba_borrar_alertas_invalida_el_stream_y_d2_resincroniza(base_d2: Any) -> None:
    """Re-sembrar con 01-colecciones.js borra `alertas`: D2 no debe girar sobre un cursor muerto."""
    with ServidorSmtp() as smtp:
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)) as supervisor:
            await insertar(base_d2, 1)
            await token_en(base_d2, "TXN-000001")
            await base_d2.drop_collection("alertas")

            async def aviso() -> bool:
                error = supervisor.informe().tarea(NOMBRE_D2).ultimo_error
                return error is not None and "se invalido" in error.mensaje

            await esperar(aviso, "el aviso de resincronizacion por invalidate")
            await asyncio.sleep(0.5)  # D2 reabre desde ahora
            await base_d2["alertas"].insert_one(a_entero_64(alerta_documento(2)))
            await token_en(base_d2, "TXN-000002")
        recibidos = sorted(m["X-Centinela-Alerta"] for m in smtp.mensajes())

    assert recibidos == ["ALR-000001", "ALR-000002"]


# ---------------------------------------------------------------------------
# Un correo que falla
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_un_correo_que_falla_deja_la_notificacion_fallida_y_d2_sigue(base_d2: Any) -> None:
    ajustes = ajustes_de_prueba(puerto_cerrado())  # nadie escucha: Connection refused
    async with d2_corriendo(base_d2, ajustes) as supervisor:
        await insertar(base_d2, 1, 2)
        await token_en(base_d2, "TXN-000002")
        fila = supervisor.informe().tarea(NOMBRE_D2)

    notas = {n["_id"]: n async for n in base_d2["notificaciones"].find({})}
    assert set(notas) == {"NOT-000001", "NOT-000002"}
    for nota in notas.values():
        assert nota["estado_envio"] == "fallido"
        assert nota["intentos"] == ajustes.reintentos_envio, "el tope corta los reintentos"
        assert "ConnectionRefusedError" in nota["detalle_error"]
    assert fila.estado == "corriendo", "un correo que falla no detiene a D2"
    assert fila.ultimo_error is not None and "ConnectionRefusedError" in fila.ultimo_error.mensaje
    assert await base_d2["deteccion_estado"].count_documents({"clase": "descarte"}) == 0


@pytest.mark.asyncio
async def prueba_un_servidor_que_rechaza_deja_la_notificacion_fallida(base_d2: Any) -> None:
    with ServidorSmtp(rechazar=True) as smtp:
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)):
            await insertar(base_d2, 1)
            await token_en(base_d2, "TXN-000001")

    nota = await base_d2["notificaciones"].find_one({"_id": "NOT-000001"})
    assert nota["estado_envio"] == "fallido"
    assert "SMTP" in nota["detalle_error"]


# ---------------------------------------------------------------------------
# El indice, el validador y la lectura
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_indice_unico_frena_la_segunda_notificacion_aunque_el_id_sea_otro(base_d2: Any) -> None:
    nota = {
        "_id": "NOT-000001", "alerta_id": "ALR-000001", "canal": "correo",
        "estado_envio": "enviado", "fecha": alerta_documento()["fecha_creacion"],
        "esquema_version": 1,
    }
    await base_d2["notificaciones"].insert_one(nota)

    with pytest.raises(DuplicateKeyError):
        await base_d2["notificaciones"].insert_one({**nota, "_id": "NOT-000900"})
    # Otro canal para la misma alerta si cabe: la clave es (alerta_id, canal).
    await base_d2["notificaciones"].insert_one({**nota, "_id": "NOT-000901", "canal": "sms"})


@pytest.mark.asyncio
async def prueba_el_validador_rechaza_un_campo_que_no_declara(base_d2: Any) -> None:
    with pytest.raises(WriteError):
        await base_d2["notificaciones"].insert_one(
            {
                "_id": "NOT-000001", "alerta_id": "ALR-000001", "canal": "correo",
                "estado_envio": "pendiente", "fecha": alerta_documento()["fecha_creacion"],
                "esquema_version": 1, "campo_inventado": 1,
            }
        )


@pytest.mark.asyncio
async def prueba_la_lectura_une_la_alerta_ordena_filtra_y_pagina(base_d2: Any) -> None:
    with ServidorSmtp() as smtp:
        async with d2_corriendo(base_d2, ajustes_de_prueba(smtp.puerto)):
            await insertar(base_d2, 1, 2, 3)
            await token_en(base_d2, "TXN-000003")
    await base_d2["notificaciones"].update_one(
        {"_id": "NOT-000002"},
        {"$set": {"estado_envio": "fallido", "detalle_error": "boom"}},
    )
    # Una huerfana: su alerta ya no existe.
    await base_d2["alertas"].delete_one({"_id": "ALR-000001"})
    lectura = RepoLecturaNotificaciones(base_d2)

    todas, total = await lectura.listar(estado_envio=None, limite=10, desplazamiento=0)
    assert total == 3
    assert {d["_id"] for d in todas} == {"NOT-000001", "NOT-000002", "NOT-000003"}
    fechas = [d["fecha"] for d in todas]
    assert fechas == sorted(fechas, reverse=True), "las mas recientes primero"
    por_id = {d["_id"]: d for d in todas}
    assert por_id["NOT-000003"]["alerta"][0]["cliente_nombre"] == "Maria Rodriguez Vargas"
    assert por_id["NOT-000003"]["alerta"][0]["monto_crc"] == 280_000
    assert por_id["NOT-000001"]["alerta"] == [], "alerta borrada: sale sin sus datos"

    fallidas, total_fallidas = await lectura.listar(estado_envio="fallido", limite=10, desplazamiento=0)
    assert (total_fallidas, [d["_id"] for d in fallidas]) == (1, ["NOT-000002"])

    segunda, total_pagina = await lectura.listar(estado_envio=None, limite=2, desplazamiento=2)
    assert total_pagina == 3 and len(segunda) == 1


# ---------------------------------------------------------------------------
# D2 dentro de la aplicacion
# ---------------------------------------------------------------------------


async def _estado_de_d2(cliente: Any) -> dict[str, Any]:
    cuerpo = (await cliente.get("/estado", headers=encabezados_de("supervisor"))).json()
    return next(tarea for tarea in cuerpo["tareas"] if tarea["nombre"] == NOMBRE_D2)


@pytest.mark.asyncio
async def prueba_el_lifespan_lanza_d2_y_estado_lo_muestra_corriendo(
    base_d2: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    with ServidorSmtp() as smtp:
        monkeypatch.setenv("CENTINELA_MONGO__BASE", BASE_D2)
        monkeypatch.setenv("CENTINELA_NOTIFICACIONES__SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("CENTINELA_NOTIFICACIONES__SMTP_PUERTO", str(smtp.puerto))
        obtener_ajustes.cache_clear()
        async with LifespanManager(crear_aplicacion("htmx")) as gestionada, AsyncClient(
            transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
        ) as cliente:

            async def con_el_flujo_abierto() -> bool:
                # El primer latido: el change stream ya esta abierto y el token guardado.
                fila = await _estado_de_d2(cliente)
                return fila["estado"] == "corriendo" and fila["eventos_procesados"] >= 1

            await esperar(con_el_flujo_abierto, "que /estado muestre a D2 corriendo y latiendo")
            fila = await _estado_de_d2(cliente)
            assert fila["estado"] == "corriendo"
            assert fila["historia"] == "H-11"

            await insertar(base_d2, 1)
            await token_en(base_d2, "TXN-000001")
            assert len(smtp.mensajes()) == 1

            lista = (await cliente.get("/api/v1/notificaciones", headers=encabezados_de("supervisor"))).json()
            assert [e["alerta_id"] for e in lista["elementos"]] == ["ALR-000001"]
            assert lista["elementos"][0]["estado_envio"] == "enviado"
            assert lista["elementos"][0]["cliente_nombre"] == "Maria Rodriguez Vargas"


@pytest.mark.asyncio
async def prueba_con_notificaciones_apagadas_d2_no_se_lanza(
    base_d2: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CENTINELA_MONGO__BASE", BASE_D2)
    monkeypatch.setenv("CENTINELA_NOTIFICACIONES__ACTIVA", "false")
    obtener_ajustes.cache_clear()
    async with LifespanManager(crear_aplicacion("htmx")) as gestionada, AsyncClient(
        transport=ASGITransport(app=gestionada.app), base_url="http://pruebas"
    ) as cliente:
        fila = await _estado_de_d2(cliente)

    assert fila["estado"] == "no_construida"
