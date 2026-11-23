"""H-25: contrasenas cifradas y tokens firmados. Sin Mongo ni HTTP."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.modelos.alertas import RolAgente
from app.seguridad import contrasenas, tokens
from app.seguridad.permisos import PERMISOS_POR_ROL, Permiso, rol_tiene
from app.seguridad.tokens import TokenInvalido

CLAVE = b"una-clave-de-firma-de-prueba-de-32-bytes!"
AHORA = datetime(2026, 11, 22, 15, 0, tzinfo=UTC)

# Parametros baratos: la prueba verifica el mecanismo, no el costo de produccion.
BARATOS = {"algoritmo": "scrypt", "n": 2**10, "r": 8, "p": 1, "dklen": 32}


# ---------------------------------------------------------------------------
# Contrasenas
# ---------------------------------------------------------------------------


def prueba_la_contrasena_correcta_verifica_y_la_incorrecta_no() -> None:
    credencial = contrasenas.cifrar("contrasena-larga-1", BARATOS)

    assert contrasenas.verificar("contrasena-larga-1", credencial)
    assert not contrasenas.verificar("contrasena-larga-2", credencial)
    assert not contrasenas.verificar("", credencial)


def prueba_la_credencial_no_contiene_la_contrasena_y_lleva_sus_parametros() -> None:
    credencial = contrasenas.cifrar("contrasena-larga-1", BARATOS)

    assert set(credencial) == {"hash", "sal", "parametros"}
    assert "contrasena-larga-1" not in json.dumps(credencial)
    assert credencial["parametros"] == BARATOS


def prueba_dos_cifrados_de_la_misma_contrasena_difieren_por_la_sal() -> None:
    uno = contrasenas.cifrar("contrasena-larga-1", BARATOS)
    otro = contrasenas.cifrar("contrasena-larga-1", BARATOS)

    assert uno["sal"] != otro["sal"] and uno["hash"] != otro["hash"]


def prueba_cada_credencial_se_verifica_con_los_parametros_con_que_se_creo() -> None:
    vieja = contrasenas.cifrar("contrasena-larga-1", BARATOS)
    nueva = contrasenas.cifrar("contrasena-larga-1")

    assert nueva["parametros"]["n"] > vieja["parametros"]["n"]
    assert contrasenas.verificar("contrasena-larga-1", vieja)
    assert contrasenas.verificar("contrasena-larga-1", nueva)


@pytest.mark.parametrize(
    "credencial",
    [
        None,
        {},
        {"hash": "x", "sal": "y", "parametros": {}},
        {"hash": "!!", "sal": "!!", "parametros": BARATOS},
        {"hash": "AA==", "sal": "AA==", "parametros": {**BARATOS, "n": 2**30}},
        {"hash": "AA==", "sal": "AA==", "parametros": {**BARATOS, "algoritmo": "md5"}},
    ],
    ids=["ausente", "vacia", "parametros-vacios", "base64-roto", "n-gigante", "otro-algoritmo"],
)
def prueba_una_credencial_ausente_o_danada_nunca_verifica_ni_lanza(credencial: dict | None) -> None:
    assert contrasenas.verificar("contrasena-larga-1", credencial) is False


def prueba_el_largo_minimo_se_exige_al_crear() -> None:
    assert contrasenas.problemas_de_contrasena("corta")
    assert not contrasenas.problemas_de_contrasena("x" * contrasenas.LARGO_MINIMO)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def _emitir(rol: RolAgente = RolAgente.SUPERVISOR) -> str:
    token, _ = tokens.emitir(CLAVE, "AGT-001", rol, minutos=30, ahora=AHORA)
    return token


def prueba_un_token_valido_devuelve_agente_rol_y_expiracion() -> None:
    token, expira = tokens.emitir(CLAVE, "AGT-001", RolAgente.SUPERVISOR, minutos=30, ahora=AHORA)

    sesion = tokens.verificar(CLAVE, token, ahora=AHORA + timedelta(minutes=29))

    assert sesion.agente_id == "AGT-001"
    assert sesion.rol == RolAgente.SUPERVISOR
    assert sesion.expira == expira == AHORA + timedelta(minutes=30)


def prueba_un_token_expirado_se_rechaza() -> None:
    with pytest.raises(TokenInvalido) as fallo:
        tokens.verificar(CLAVE, _emitir(), ahora=AHORA + timedelta(minutes=30))

    assert fallo.value.motivo == "expirado"


def prueba_una_firma_alterada_se_rechaza() -> None:
    carga, firma = _emitir().split(".")
    alterada = f"{carga}.{'A' if firma[0] != 'A' else 'B'}{firma[1:]}"

    with pytest.raises(TokenInvalido) as fallo:
        tokens.verificar(CLAVE, alterada, ahora=AHORA)

    assert fallo.value.motivo == "firma"


def prueba_un_token_firmado_con_otra_clave_se_rechaza() -> None:
    with pytest.raises(TokenInvalido):
        tokens.verificar(b"otra-clave-distinta-de-32-bytes-xxxxx", _emitir(), ahora=AHORA)


def prueba_subir_el_rol_en_la_carga_rompe_la_firma() -> None:
    """El ataque tipico: tomar un token de agente y cambiarle el rol a administrador."""
    carga, firma = _emitir(RolAgente.AGENTE).split(".")
    datos = json.loads(base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4)))
    datos["rol"] = "administrador"
    falsa = (
        base64.urlsafe_b64encode(json.dumps(datos, separators=(",", ":")).encode())
        .rstrip(b"=")
        .decode()
    )

    with pytest.raises(TokenInvalido) as fallo:
        tokens.verificar(CLAVE, f"{falsa}.{firma}", ahora=AHORA)

    assert fallo.value.motivo == "firma"


def prueba_un_rol_desconocido_firmado_con_la_clave_buena_tambien_se_rechaza() -> None:
    carga = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"sub": "AGT-001", "rol": "root", "iat": 0, "exp": 4_000_000_000}
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    token = f"{carga}.{tokens._firmar(CLAVE, carga)}"

    with pytest.raises(TokenInvalido) as fallo:
        tokens.verificar(CLAVE, token, ahora=AHORA)

    assert fallo.value.motivo == "carga"


@pytest.mark.parametrize("basura", ["", "x", "a.b.c", ".", "a.", ".b", "ñ.ñ"])
def prueba_un_token_con_formato_roto_se_rechaza(basura: str) -> None:
    with pytest.raises(TokenInvalido):
        tokens.verificar(CLAVE, basura, ahora=AHORA)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def prueba_cada_rol_amplia_al_anterior_y_el_administrador_lo_tiene_todo() -> None:
    agente = PERMISOS_POR_ROL[RolAgente.AGENTE]
    supervisor = PERMISOS_POR_ROL[RolAgente.SUPERVISOR]

    assert agente < supervisor
    assert supervisor - agente == {
        Permiso.TABLERO,
        Permiso.VER_REGLAS,
        Permiso.EDITAR_REGLAS,
        Permiso.GESTIONAR_LISTAS,
    }
    assert all(rol_tiene(RolAgente.ADMINISTRADOR, permiso) for permiso in Permiso)
    assert not rol_tiene(RolAgente.AGENTE, Permiso.TABLERO)
