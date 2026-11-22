"""Flujo transaccional y reglas: transacciones, reglas_deteccion, listas_riesgo.

Se corresponde con `scripts/practica1/b-transaccional.js` (persona B).

Nota de nombres: donde el scaffold de la Practica 1 dice `monto` y `fecha`, aca
dicen `monto_crc` (mas `moneda`) y `fecha`, con la regla de dinero e ISODate en UTC.
Ver docs/06-plataforma.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from app.modelos.base import (
    CuentaId,
    CuentaSinpe,
    DocumentoBase,
    MarcaTiempoUTC,
    ModeloCentinela,
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
    # Etiqueta oculta del generador ({fraude, patron, episodio}). Solo sirve para medir
    # el motor: ninguna regla ni D1 la leen, y no existe en datos reales.
    simulacion: dict[str, Any] | None = None


class VersionAnteriorRegla(ModeloCentinela):
    """Una version de la regla que ya no rige, tal como estaba justo antes de editarse.

    La escribe H-16 (`app/repos/reglas.py`, `instantanea`) en el mismo documento de la
    regla. Con esto una alerta vieja se explica con la regla con que se evaluo: la
    alerta v2 guarda `regla_id` y `version`, y la version se busca aca si ya no es la
    vigente.
    """

    codigo: str
    descripcion: str
    tipo: str
    umbral: float | None = None
    parametros: dict[str, int | float] = Field(default_factory=dict)
    peso: int
    activa: bool
    version: int = Field(ge=1)
    esquema_version: int = Field(ge=1)
    reemplazada_en: MarcaTiempoUTC = Field(description="Cuando dejo de regir, en UTC")


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
    # D-14: la historia de la regla es parte del documento, y el modelo la declara.
    # Antes la publicaba solo la API (`ReglaConHistoria`); declararla aqui es lo que
    # deja a la migracion de alertas y a H-18 explicar una alerta vieja con la version
    # de la regla con que se evaluo, leyendo el mismo modelo que el motor.
    versiones_anteriores: list[VersionAnteriorRegla] = Field(
        default_factory=list,
        description="Versiones que ya no rigen, de la mas vieja a la mas reciente",
    )

    def en_version(self, version: int) -> VersionAnteriorRegla | Regla | None:
        """La regla tal como estaba en `version`: la vigente, una anterior o None.

        None quiere decir que esa version ya no existe en el documento (por ejemplo,
        re-sembrar `03-catalogos.js` borra la historia: D-15).
        """
        if self.version == version:
            return self
        return next((v for v in self.versiones_anteriores if v.version == version), None)


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
