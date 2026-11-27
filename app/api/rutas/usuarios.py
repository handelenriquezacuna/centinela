"""Usuarios del portal, solo lectura (T-83): quien puede entrar y que cuentas estan bloqueadas.

`GET /api/v1/usuarios`, solo administrador. No hay alta, baja ni desbloqueo por la API:
eso es `python tareas.py usuarios` (el portal solo muestra el comando). Nunca sale la
credencial, ni siquiera cifrada: el repositorio ni la trae.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, status
from pydantic import Field

from app.api.errores import respuestas_documentadas
from app.api.rutas.acceso import RepoAgentesDep
from app.modelos import ModeloCentinela
from app.modelos.alertas import RolAgente
from app.seguridad.bloqueo import esta_bloqueada

enrutador = APIRouter(tags=["usuarios"])


class UsuarioPortal(ModeloCentinela):
    agente_id: str = Field(examples=["AGT-001"])
    nombre: str
    rol: RolAgente
    activo: bool
    tiene_credencial: bool = Field(description="Sin credencial el agente no puede iniciar sesion")
    intentos_fallidos: int = Field(ge=0, description="Intentos fallidos seguidos desde el ultimo acierto")
    bloqueado: bool = Field(description="Hay un bloqueo temporal vigente")
    bloqueado_hasta: datetime | None = Field(
        default=None, description="UTC; presente solo mientras el bloqueo esta vigente"
    )


class ListaUsuarios(ModeloCentinela):
    elementos: list[UsuarioPortal]
    total: int = Field(ge=0)
    bloqueados: int = Field(ge=0)


@enrutador.get(
    "/usuarios",
    response_model=ListaUsuarios,
    summary="Los usuarios del portal y cuales estan bloqueados por intentos fallidos",
    responses=respuestas_documentadas(status.HTTP_403_FORBIDDEN),
)
async def listar_usuarios(agentes: RepoAgentesDep) -> ListaUsuarios:
    ahora = datetime.now(UTC)
    elementos: list[UsuarioPortal] = []
    for documento in await agentes.listar():
        bloqueado = esta_bloqueada(documento, ahora)
        hasta = documento.get("bloqueado_hasta")
        elementos.append(
            UsuarioPortal(
                agente_id=documento["_id"],
                nombre=documento["nombre"],
                rol=RolAgente(documento["rol"]),
                activo=bool(documento.get("activo", True)),
                tiene_credencial=bool(documento.get("tiene_credencial", False)),
                intentos_fallidos=int(documento.get("intentos_fallidos", 0) or 0),
                bloqueado=bloqueado,
                bloqueado_hasta=hasta if bloqueado else None,
            )
        )
    return ListaUsuarios(
        elementos=elementos,
        total=len(elementos),
        bloqueados=sum(1 for usuario in elementos if usuario.bloqueado),
    )
