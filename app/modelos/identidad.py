"""Identidad y comportamiento: clientes, cuentas, perfiles_comportamiento.

Se corresponde con `scripts/practica1/a-identidad.js` (persona A).

Nota de nombres: donde el scaffold de la Practica 1 dice `promedio`, `maximo` y
`desviacion`, aca dicen `promedio_crc`, `maximo_crc` y `desviacion_crc`, porque la
regla de dinero exige el sufijo y el entero. Ver docs/06-plataforma.md.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from app.modelos.base import (
    Cedula,
    ClienteId,
    CuentaId,
    CuentaSinpe,
    DocumentoBase,
    MarcaTiempoUTC,
    ModeloCentinela,
    MontoCRC,
)

HoraDelDia = Annotated[
    str,
    StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$"),
    Field(description="Hora local de Costa Rica, formato HH:MM"),
]


class Cliente(DocumentoBase):
    """Cliente bancario. Es el sujeto de los datos, no un usuario del sistema."""

    cliente_id: ClienteId = Field(alias="_id")
    nombre: str = Field(min_length=1)
    cedula: Cedula
    fecha_registro: MarcaTiempoUTC


class Cuenta(DocumentoBase):
    """Cuenta propia del banco.

    `cliente_id` es referencia y no documento embebido: una cuenta puede cambiar de
    titular, y el cliente no debe cargar con sus cuentas adentro.

    Una cuenta mula NO tiene documento aqui: viaja como texto en
    `transacciones.cuenta_destino` y, si se identifica, como entrada en
    `listas_riesgo`.
    """

    cuenta_id: CuentaId = Field(alias="_id")
    cuenta_sinpe: CuentaSinpe
    cliente_id: ClienteId


class HorarioHabitual(ModeloCentinela):
    """Ventana en la que el cliente suele operar, en hora local.

    Es hora local a proposito, y es la unica cosa del sistema que no es UTC junto
    con `indicadores_diarios.fecha`: "opera de 07:00 a 19:00" es una afirmacion
    sobre el dia del cliente, no sobre un instante.
    """

    inicio: HoraDelDia
    fin: HoraDelDia


class PerfilComportamiento(DocumentoBase):
    """Perfil materializado del cliente. Lo produce el pipeline `$merge` de H-21.

    El `_id` es el `cliente_id` (CLI-001), no un identificador propio: es una
    coleccion materializada de un perfil por cliente, y con `_id` natural el
    `$merge` de H-21 es idempotente sin trabajo extra.
    """

    cliente_id: ClienteId = Field(
        alias="_id", description="El _id ES el cliente_id: un perfil por cliente"
    )
    promedio_crc: MontoCRC
    maximo_crc: MontoCRC
    # Opcional porque el validador de `perfiles_comportamiento` todavia no la declara
    # (CONTRATO-IDS.md si la menciona). La calcula el pipeline de H-21; hasta que el
    # validador la acepte, no se escribe.
    desviacion_crc: MontoCRC | None = None
    num_transferencias: int = Field(ge=0)
    horario_habitual: HorarioHabitual
    destinos_frecuentes: list[CuentaSinpe] = Field(default_factory=list)
    # Lo llena H-21 al materializar. Opcional porque en la Practica 1 el perfil se
    # inserta a mano y todavia no hay pipeline que lo calcule.
    calculado_en: MarcaTiempoUTC | None = None
