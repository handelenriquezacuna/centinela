"""El portal del cliente (T-82): el enlace del SMS y la bandeja del simulador de telefono.

Lo que el cliente ve y hace, todo bajo `/c/{token}` y `/api/v1/cliente/{token}`:

- `GET /c/{token}`: la pagina (el `index.html` de la SPA). Siempre responde lo mismo, exista
  o no el enlace: la decision la toma la API, y la pagina no filtra nada.
- `GET /api/v1/cliente/{token}`: la transferencia (monto, hora, destino enmascarado) y, si ya
  respondio, el estado de su caso. La pagina lo pregunta cada pocos segundos.
- `POST /api/v1/cliente/{token}/respuesta`: "Si, fui yo" o "No, no fui yo".

Estas tres rutas son PUBLICAS: no hay sesion de agente, el token es la autorizacion. Por eso:

- Un enlace inexistente, vencido sin responder o mal formado da el MISMO 404 con el mismo
  cuerpo (y cuenta contra el limite de la IP): no se puede saber cual de los tres fue.
- Responder gasta el enlace (una transaccion, ver `RepoRespuestaCliente`). Un segundo intento
  es 409. Despues de responder, el mismo enlace sigue siendo de solo lectura durante
  `aviso_cliente.seguimiento_horas`, para mostrar el estado del caso.
- La alerta NO se cierra sola: la respuesta es evidencia para el agente.
- Cada respuesta y cada enlace invalido queda en el log `centinela.auditoria` (sin el token).
- Cabeceras `Cache-Control: no-store` y `Referrer-Policy: no-referrer`: el token viaja en la
  direccion y no debe quedar en caches ni en el `Referer` de ningun enlace.

Tiempo real: la pagina hace SONDEO corto (`aviso_cliente.sondeo_segundos`), no SSE. El canal SSE
del panel abre un change stream por conexion; ofrecerlo sin sesion permitiria a cualquiera abrir
cursores en la base. El sondeo es una lectura por indice, acotada por el limite de abuso, y para
un caso que cambia cada minutos es indistinguible de lo instantaneo.

`GET /api/v1/simulador-sms` (supervisor y administrador) alimenta la bandeja de telefono de la
demo; solo existe con la bandera `aviso_cliente.simulador_sms` (por omision, solo en `dev`).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response, status

from app.api.cliente import (
    EnlaceNoValido,
    ahora_utc,
    armar_enlace,
    cargar,
    mensaje_de_bandeja,
)
from app.api.dependencias import AjustesDep, LimitadorDep, RepoRespuestaClienteDep
from app.api.errores import (
    CodigoError,
    DetalleError,
    ErrorCentinela,
    respuestas_documentadas,
)
from app.api.esquemas_cliente import (
    BandejaSms,
    CuerpoRespuestaCliente,
    EnlaceDelCliente,
)
from app.api.rutas.spa import respuesta_spa
from app.repos.aviso_cliente import AlertaDelEnlaceNoExiste, EnlaceNoDisponible
from app.seguridad.enlaces import EstadoEnlace

registro_auditoria = logging.getLogger("centinela.auditoria")

enrutador = APIRouter(tags=["cliente"])
enrutador_pagina = APIRouter(include_in_schema=False)

TokenRuta = Annotated[
    str, Path(max_length=200, description="El token del enlace del SMS; es la autorizacion")
]
CABECERAS_PRIVADAS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _privada(respuesta: Response) -> None:
    respuesta.headers.update(CABECERAS_PRIVADAS)


def _ip(peticion: Request) -> str:
    return peticion.client.host if peticion.client else "desconocida"


def _no_valido(ayuda: str) -> ErrorCentinela:
    return ErrorCentinela(
        CodigoError.NO_ENCONTRADO,
        "Este enlace no es válido o ya venció.",
        status.HTTP_404_NOT_FOUND,
        [DetalleError(campo="telefono_ayuda", mensaje=ayuda)],
    )


@enrutador_pagina.get("/c/{token}")
async def pagina_del_cliente(token: str) -> Response:
    """La pagina del cliente: el `index.html` de la SPA, igual para cualquier token."""
    respuesta = respuesta_spa()
    respuesta.headers.update(CABECERAS_PRIVADAS)
    return respuesta


async def _cargar_o_negar(
    token: str, peticion: Request, repo: RepoRespuestaClienteDep, ajustes: AjustesDep, limitador: LimitadorDep
):
    ip = _ip(peticion)
    if limitador.ip_bloqueada(ip):
        raise ErrorCentinela(
            CodigoError.DEMASIADOS_INTENTOS,
            "Demasiados intentos. Espera un momento antes de volver a intentar.",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )
    try:
        return await cargar(repo, token, ahora=ahora_utc(), ajustes=ajustes.aviso_cliente)
    except EnlaceNoValido:
        limitador.anotar_invalido(ip)
        registro_auditoria.warning("aviso_cliente_enlace_invalido ip=%s", ip)
        raise _no_valido(ajustes.aviso_cliente.telefono_ayuda) from None


@enrutador.get(
    "/cliente/{token}",
    response_model=EnlaceDelCliente,
    summary="El enlace del SMS: la transferencia y, si ya respondio, el estado de su caso (publica)",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_429_TOO_MANY_REQUESTS,
    ),
)
async def ver_enlace(
    token: TokenRuta,
    peticion: Request,
    respuesta: Response,
    repo: RepoRespuestaClienteDep,
    ajustes: AjustesDep,
    limitador: LimitadorDep,
) -> EnlaceDelCliente:
    """Sin sesion: el token es la autorizacion. Inexistente, vencido o mal formado: el mismo 404."""
    _privada(respuesta)
    notificacion, alerta, estado, _ = await _cargar_o_negar(
        token, peticion, repo, ajustes, limitador
    )
    return armar_enlace(
        alerta=alerta, notificacion=notificacion, estado=estado, ajustes=ajustes.aviso_cliente
    )


@enrutador.post(
    "/cliente/{token}/respuesta",
    response_model=EnlaceDelCliente,
    summary="Responder 'si fui yo' o 'no fui yo'; gasta el enlace (publica)",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_429_TOO_MANY_REQUESTS,
    ),
)
async def responder(
    token: TokenRuta,
    cuerpo: CuerpoRespuestaCliente,
    peticion: Request,
    respuesta: Response,
    repo: RepoRespuestaClienteDep,
    ajustes: AjustesDep,
    limitador: LimitadorDep,
) -> EnlaceDelCliente:
    """La respuesta queda en el historial de la alerta y en la notificacion; el estado no cambia.

    409 si el enlace ya se uso o el caso ya esta cerrado. La alerta la sigue decidiendo el agente.
    """
    _privada(respuesta)
    ip = _ip(peticion)
    notificacion, alerta, estado, enlace_hash = await _cargar_o_negar(
        token, peticion, repo, ajustes, limitador
    )
    if not limitador.intento_de_respuesta(enlace_hash, ip):
        raise ErrorCentinela(
            CodigoError.DEMASIADOS_INTENTOS,
            "Demasiados intentos con este enlace. Espera un momento.",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )
    previa = armar_enlace(
        alerta=alerta, notificacion=notificacion, estado=estado, ajustes=ajustes.aviso_cliente
    )
    if estado is not EstadoEnlace.VIGENTE or not previa.puede_responder:
        raise ErrorCentinela(
            CodigoError.CONFLICTO,
            "Este enlace ya no admite respuesta: ya respondiste o el caso ya se cerró.",
            status.HTTP_409_CONFLICT,
        )
    try:
        await repo.registrar_respuesta(enlace_hash, cuerpo.valor, ahora_utc())
    except (EnlaceNoDisponible, AlertaDelEnlaceNoExiste):
        raise ErrorCentinela(
            CodigoError.CONFLICTO,
            "Este enlace ya no admite respuesta: ya respondiste o venció.",
            status.HTTP_409_CONFLICT,
        ) from None
    registro_auditoria.info(
        "aviso_cliente_respuesta alerta=%s notificacion=%s valor=%s ip=%s",
        notificacion["alerta_id"],
        notificacion["_id"],
        cuerpo.valor,
        ip,
    )
    notificacion, alerta, estado, _ = await cargar(
        repo, token, ahora=ahora_utc(), ajustes=ajustes.aviso_cliente
    )
    return armar_enlace(
        alerta=alerta, notificacion=notificacion, estado=estado, ajustes=ajustes.aviso_cliente
    )


@enrutador.get(
    "/simulador-sms",
    response_model=BandejaSms,
    summary="Bandeja del telefono simulado: los SMS al cliente (solo con la bandera del demo)",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND, status.HTTP_422_UNPROCESSABLE_CONTENT
    ),
)
async def bandeja(
    ajustes: AjustesDep,
    repo: RepoRespuestaClienteDep,
    limite: Annotated[int, Query(ge=1, le=100, description="SMS mas recientes")] = 30,
) -> BandejaSms:
    """404 si `aviso_cliente.simulador_sms` esta apagado (por omision, fuera de `dev`)."""
    if not ajustes.simulador_sms_activo:
        raise ErrorCentinela(
            CodigoError.NO_ENCONTRADO,
            "El simulador de SMS está desactivado en este entorno.",
            status.HTTP_404_NOT_FOUND,
        )
    ahora = ahora_utc()
    return BandejaSms(
        mensajes=[mensaje_de_bandeja(d, ahora=ahora) for d in await repo.bandeja_sms(limite)]
    )
