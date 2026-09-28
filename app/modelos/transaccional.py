"""Flujo transaccional y reglas: transacciones, reglas_deteccion, listas_riesgo.

Se corresponde con `scripts/practica1/b-transaccional.js` (persona B).

Nota de nombres: donde el scaffold de la Practica 1 dice `monto` y `fecha`, aca
dicen `monto_crc` (mas `moneda`) y `fecha`, con la regla de dinero e ISODate en UTC.
Ver docs/06-plataforma.md.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from app.modelos.base import (
    CuentaId,
    CuentaSinpe,
    DocumentoBase,
    MarcaTiempoUTC,
    Moneda,
    MontoCRC,
    ReglaId,
    RiesgoId,
    TransaccionId,
)


class Canal(StrEnum):
    """Canal por el que entro la operacion.

    Hoy solo existe `sinpe_movil`, y es el unico valor que acepta el validador
    `$jsonSchema` de `transacciones`. Agregar un canal es cambiar el enum Y el
    validador en el mismo commit: el payload de cada canal es distinto, y es una de
    las razones por las que el modelo documental gana aca (ver docs/01, seccion 6).
    """

    SINPE_MOVIL = "sinpe_movil"


class Transaccion(DocumentoBase):
    """Transferencia observada. Se escribe una vez y se lee completa; nunca se
    actualiza por partes. Es la definicion de agregado.

    La ingesta NO pasa por la API: el generador escribe directo en Mongo y el motor
    reacciona al oplog. Este modelo existe para validar antes de escribir y para
    tipar lo que se lee, no para recibir un POST.
    """

    transaccion_id: TransaccionId = Field(alias="_id")
    cuenta_origen: CuentaId
    # Texto y no CuentaId a proposito: el destino puede ser una cuenta mula, que no
    # tiene documento en `cuentas`.
    cuenta_destino: CuentaSinpe
    monto_crc: MontoCRC
    moneda: Moneda = "CRC"
    canal: Canal = Canal.SINPE_MOVIL
    fecha: MarcaTiempoUTC


class Regla(DocumentoBase):
    """Regla de deteccion. Vive en la base, no en el codigo: cambiar un umbral no
    requiere desplegar.

    `umbral` y `peso` SI son numeros de negocio, y por eso estan aqui, en Mongo, y
    no en `config/centinela.yml`.

    `version` es la que la alerta copia al dispararse (H-16 versiona al editar): la
    alerta tiene que poder explicarse dentro de un ano aunque la regla ya cambio.
    """

    regla_id: ReglaId = Field(alias="_id")
    codigo: str = Field(pattern=r"^R-\d{2}$", description="Codigo corto, R-01")
    descripcion: str = Field(min_length=1)
    tipo: str = Field(min_length=1, description="Familia de la regla: monto, destino, horario")
    # El validador de `reglas_deteccion` lo declara obligatorio, pero el modelo lo
    # deja opcional: quien decide el parametro de cada regla es H-07, no la
    # plataforma. Los datos de demo usan 10 para R-01 (diez veces el promedio) y 0
    # para las reglas que no comparan contra un numero.
    umbral: float | None = Field(
        default=None,
        description="Parametro de la regla. NO es dinero: puede ser un multiplo (10x) o una hora",
    )
    # Segunda perilla de la regla, cuando un solo numero no alcanza: R-09 es "3
    # transferencias en 30 minutos", R-03 mira 90 dias, R-11 usa ventana de 15 minutos
    # y 2 saltos. Sin este campo, H-08 tendria que codificar esas constantes en
    # Python, y las reglas dejarian de vivir en datos.
    parametros: dict[str, int | float] = Field(default_factory=dict)
    peso: int = Field(ge=0, le=100, description="Aporte al puntaje cuando dispara")
    activa: bool = True
    # Opcional porque el validador de `reglas_deteccion` todavia no la declara. La
    # necesita H-16 (al editar una regla se versiona la anterior) y H-08 (la alerta
    # copia la version con la que se evaluo). Hasta que el validador la acepte, no se
    # escribe: el documento en Mongo manda.
    version: int | None = Field(default=None, ge=1)


class EntradaListaRiesgo(DocumentoBase):
    """Cuenta senalada como mula.

    Se llama `EntradaListaRiesgo` y no `ListaRiesgo` porque el documento es una
    entrada de la lista, no la lista.
    """

    riesgo_id: RiesgoId = Field(alias="_id")
    cuenta: CuentaSinpe
    motivo: str = Field(min_length=1)
    fecha_reporte: MarcaTiempoUTC
    vigente: bool = True
