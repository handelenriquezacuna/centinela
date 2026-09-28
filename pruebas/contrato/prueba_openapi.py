"""El contrato HTTP publicado como OpenAPI.

El esquema publicado tiene que decir la verdad. El riesgo concreto que estas pruebas
cubren: FastAPI documenta por omision que un 422 devuelve `HTTPValidationError`, pero
en tiempo de ejecucion Centinela devuelve `RespuestaError`. Un portal generado desde
ese esquema fallaria al leer el error.
"""

from __future__ import annotations

import pytest
from fastapi import status
from httpx import AsyncClient

from app.main import crear_aplicacion

ESQUEMA = crear_aplicacion().openapi()


def prueba_el_esquema_se_genera() -> None:
    assert ESQUEMA["openapi"].startswith("3.")
    assert ESQUEMA["info"]["title"] == "Centinela"


def prueba_estan_publicadas_las_cuatro_rutas() -> None:
    """El inventario completo. Que sea una igualdad y no un `>=` es a proposito:
    publicar una ruta que nadie decidio es tan malo como no publicar una decidida."""
    assert set(ESQUEMA["paths"]) == {
        "/salud",
        "/estado",
        "/api/v1/alertas",
        "/api/v1/alertas/flujo",
    }


def prueba_salud_y_estado_son_rutas_distintas() -> None:
    """Las dos rutas de operacion, con dos respuestas distintas.

    Estan separadas porque responden preguntas distintas: `/salud` es "el proceso vive
    y la base contesta" y `/estado` es "el sistema esta haciendo su trabajo". Un solo
    endpoint esconde el escenario en el que el proceso vive y el detector esta caido.
    """
    salud = referencia_de_respuesta("/salud", status.HTTP_200_OK)
    estado = referencia_de_respuesta("/estado", status.HTTP_200_OK)

    assert salud.endswith("/Salud")
    assert estado.endswith("/EstadoFuncional")


def prueba_el_estado_publica_las_tareas_con_su_vocabulario() -> None:
    """El portal y quien mire `/docs` tienen que ver los estados posibles de una tarea."""
    componentes = ESQUEMA["components"]["schemas"]

    assert set(componentes["EstadoSupervision"]["enum"]) == {
        "ok",
        "degradado",
        "sin_supervision",
    }
    assert set(componentes["EstadoTarea"]["enum"]) == {
        "corriendo",
        "retrasada",
        "detenida",
        "fallida",
        "no_construida",
    }
    assert set(componentes["TareaSupervisada"]["properties"]) == {
        "nombre",
        "estado",
        "eventos_procesados",
        "ultimo_evento",
        "retraso_segundos",
        "ultimo_error",
        "historia",
    }


def prueba_el_canal_sse_se_publica_como_flujo_de_eventos() -> None:
    """El canal esta en el esquema, y con su tipo de contenido real.

    Si se publicara como `application/json`, un cliente generado desde el esquema
    intentaria leer el cuerpo entero antes de procesar el primer evento, que es
    exactamente lo que un flujo infinito no permite.
    """
    respuesta = ESQUEMA["paths"]["/api/v1/alertas/flujo"]["get"]["responses"]["200"]

    assert "text/event-stream" in respuesta["content"]
    assert "application/json" not in respuesta["content"]
    descripcion = respuesta["description"]
    assert "resincronizar" in descripcion
    assert "token de reanudacion" in descripcion


def prueba_el_canal_publica_la_cabecera_de_reanudacion() -> None:
    """`Last-Event-ID` es parte del contrato, no un detalle de implementacion."""
    parametros = {
        parametro["name"]
        for parametro in ESQUEMA["paths"]["/api/v1/alertas/flujo"]["get"]["parameters"]
    }

    assert parametros == {"Last-Event-ID", "ultimo_evento"}


def prueba_el_modelo_de_error_esta_en_el_esquema() -> None:
    componentes = ESQUEMA["components"]["schemas"]

    assert "RespuestaError" in componentes
    assert "CuerpoError" in componentes
    assert "DetalleError" in componentes

    cuerpo = componentes["CuerpoError"]
    assert set(cuerpo["required"]) >= {"codigo", "mensaje"}
    assert set(cuerpo["properties"]) == {"codigo", "mensaje", "detalles"}


def prueba_el_codigo_de_error_es_un_vocabulario_cerrado() -> None:
    assert set(ESQUEMA["components"]["schemas"]["CodigoError"]["enum"]) == {
        "parametros_invalidos",
        "no_encontrado",
        "conflicto",
        "transicion_invalida",
        "base_no_disponible",
        "error_interno",
    }


def referencia_de_respuesta(ruta: str, estado: int) -> str:
    contenido = ESQUEMA["paths"][ruta]["get"]["responses"][str(estado)]["content"]
    return contenido["application/json"]["schema"]["$ref"]


def prueba_el_422_publicado_es_el_error_de_centinela() -> None:
    """La mentira que FastAPI documenta por omision, corregida."""
    referencia = referencia_de_respuesta(
        "/api/v1/alertas", status.HTTP_422_UNPROCESSABLE_CONTENT
    )

    assert referencia.endswith("/RespuestaError")
    assert "HTTPValidationError" not in str(ESQUEMA)


def prueba_las_rutas_publican_el_500() -> None:
    for ruta in ("/salud", "/estado", "/api/v1/alertas"):
        referencia = referencia_de_respuesta(
            ruta, status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        assert referencia.endswith("/RespuestaError")


def prueba_la_respuesta_de_alertas_es_una_pagina_con_total() -> None:
    referencia = referencia_de_respuesta("/api/v1/alertas", status.HTTP_200_OK)
    assert referencia.endswith("/PaginaAlertas")

    pagina = ESQUEMA["components"]["schemas"]["PaginaAlertas"]
    assert set(pagina["properties"]) == {
        "total",
        "limite",
        "desplazamiento",
        "elementos",
    }
    assert set(pagina["required"]) == {
        "total",
        "limite",
        "desplazamiento",
        "elementos",
    }


def prueba_los_filtros_estan_publicados_como_parametros() -> None:
    parametros = {
        parametro["name"]
        for parametro in ESQUEMA["paths"]["/api/v1/alertas"]["get"]["parameters"]
    }

    assert parametros == {
        "estado",
        "severidad",
        "desde",
        "hasta",
        "limite",
        "desplazamiento",
    }


def prueba_el_filtro_de_estado_publica_el_vocabulario_de_la_maquina() -> None:
    """El portal pinta los filtros desde el esquema; el vocabulario tiene que estar."""
    assert set(ESQUEMA["components"]["schemas"]["EstadoAlerta"]["enum"]) == {
        "nueva",
        "en_revision",
        "confirmada",
        "descartada",
        "escalada",
    }
    assert set(ESQUEMA["components"]["schemas"]["Severidad"]["enum"]) == {
        "CRITICA",
        "ALTA",
        "MEDIA",
    }


def prueba_la_alerta_publicada_usa_guion_bajo_id() -> None:
    """Lo que se publica es el documento, con `_id`, no un `alerta_id` inventado."""
    alerta = ESQUEMA["components"]["schemas"]["Alerta"]

    assert "_id" in alerta["properties"]
    assert "alerta_id" not in alerta["properties"]
    assert "id" not in alerta["properties"]


def prueba_el_monto_se_publica_como_entero() -> None:
    """Dinero como `integer` en el esquema, nunca `number`.

    Un cliente generado desde un `number` usaria un flotante para dinero, y el
    contrato se rompe del lado del consumidor.
    """
    aplicacion = crear_aplicacion()
    del aplicacion  # el esquema de Transaccion no se publica todavia en H-00A

    from app.modelos import Transaccion

    propiedades = Transaccion.model_json_schema()["properties"]
    assert propiedades["monto_crc"]["type"] == "integer"
    assert propiedades["moneda"]["const"] == "CRC"


def prueba_la_descripcion_de_la_api_lleva_los_contratos() -> None:
    """La documentacion viva: quien abre /docs ve los contratos sin buscar el md."""
    descripcion = ESQUEMA["info"]["description"]

    for esperado in ("monto_crc", "esquema_version", "en_revision", "UTC"):
        assert esperado in descripcion


@pytest.mark.asyncio
async def prueba_el_esquema_se_sirve_por_http(cliente: AsyncClient) -> None:
    respuesta = await cliente.get("/openapi.json")

    assert respuesta.status_code == status.HTTP_200_OK
    assert "/api/v1/alertas" in respuesta.json()["paths"]


@pytest.mark.asyncio
async def prueba_la_documentacion_interactiva_responde(cliente: AsyncClient) -> None:
    respuesta = await cliente.get("/docs")

    assert respuesta.status_code == status.HTTP_200_OK
