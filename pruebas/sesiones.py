"""Sesiones para las pruebas: un token firmado con la clave de pruebas.

La seguridad NO se apaga en las pruebas: cada cliente HTTP lleva la cabecera
`Authorization: Bearer` de un agente con el rol que la prueba necesita. La clave es la
misma que el conftest pone en `CENTINELA_SESION__CLAVE`, asi que el token lo valida el
servicio real de sesion.
"""

from __future__ import annotations

import pytest

from app.modelos.alertas import RolAgente
from app.seguridad import permisos, tokens

CLAVE_DE_PRUEBAS = "clave-de-pruebas-0123456789-abcdefghijklmnop"


def encabezados_de(
    rol: RolAgente | str = RolAgente.ADMINISTRADOR, agente_id: str = "AGT-001"
) -> dict[str, str]:
    token, _ = tokens.emitir(
        CLAVE_DE_PRUEBAS.encode("utf-8"), agente_id, RolAgente(rol), minutos=30
    )
    return {"Authorization": f"Bearer {token}"}


def declarar_ruta_de_prueba(
    monkeypatch: pytest.MonkeyPatch,
    metodo: str,
    ruta: str,
    permiso: permisos.Permiso | None = permisos.Permiso.SESION,
) -> None:
    """Registra en la tabla una ruta que una prueba agrega a la app a proposito.

    Sin entrada en la tabla el guardia niega la ruta (falla cerrada), que es lo correcto
    para el codigo de produccion; las rutas de prueba (`/explota`) la piden por aqui y
    solo viven lo que dura la prueba.
    """
    monkeypatch.setattr(
        permisos,
        "ACCESOS",
        (*permisos.ACCESOS, permisos.Acceso(metodo, ruta, permiso)),
    )
