"""Tipos base de todo documento de Centinela.

Cambiar algo de este archivo cambia el contrato de las cuatro personas. Cada regla
de aqui tiene una prueba en `pruebas/contrato/prueba_modelos_dominio.py`.

Las cinco reglas que este modulo hace cumplir:

1. Dinero: `MontoCRC`, entero de 64 bits, colones enteros, nunca flotante.
2. Fechas: `MarcaTiempoUTC`, siempre con zona y siempre normalizada a UTC.
   Unica excepcion `FechaDia` (texto YYYY-MM-DD), para indicadores_diarios.fecha.
3. Identificadores: cadenas legibles del CONTRATO-IDS.md, nunca ObjectId.
4. `esquema_version` entero en todo documento.
5. snake_case en espanol; prohibidos `id`, `type` y `class` como nombre de campo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

# Version de esquema de los documentos que escribe este codigo. Sube a 2 cuando
# H-08 convierta alertas.reglas_disparadas en copia embebida con version; esa es la
# migracion que H-26 demuestra el 19 de octubre.
ESQUEMA_VERSION = 1

MAXIMO_ENTERO_64 = 2**63 - 1

# ---------------------------------------------------------------------------
# Dinero
# ---------------------------------------------------------------------------
# strict=True es la parte importante: sin eso Pydantic acepta 750000.0 y lo
# convierte a int, y un flotante se cuela en un campo de dinero sin que nadie lo
# note. Con strict, un flotante es un error de validacion.
#
# Ojo con el otro lado del contrato, el de la base: un int de Python que cabe en
# 32 bits se guarda como int32, no como int64. Al escribir dinero hay que envolverlo
# en bson.Int64 (Python) o NumberLong (mongosh). Ver docs/06-plataforma.md.
MontoCRC = Annotated[
    int,
    Field(strict=True, ge=0, le=MAXIMO_ENTERO_64, description="Colones enteros, sin centimos"),
]

# SINPE Movil opera solo en colones. El campo existe desde el dia 1 para que
# agregar otra moneda no sea una migracion de toda la coleccion.
Moneda = Literal["CRC"]

# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------


# La zona en la que el portal PRESENTA las fechas. La base guarda UTC siempre; esta
# constante es la otra mitad del contrato de fechas y vive aca, con los tipos base,
# para que exista una sola definicion: el fragmento del canal SSE, las plantillas del
# portal (H-17) y el conjunto de demo la toman de aqui.
#
# Es un desplazamiento fijo y no `ZoneInfo("America/Costa_Rica")` por dos razones:
# Costa Rica no aplica horario de verano desde 1992, asi que -6 es siempre correcto, y
# `zoneinfo` en Windows necesita el paquete `tzdata` instalado o levanta
# `ZoneInfoNotFoundError`. La mitad del equipo trabaja en Windows.
ZONA_COSTA_RICA = timezone(timedelta(hours=-6), name="America/Costa_Rica")


def exigir_utc(valor: datetime) -> datetime:
    """Rechaza fechas sin zona y normaliza cualquier otra a UTC.

    Una fecha sin zona es ambigua: 22:47 en San Jose y 22:47 en UTC son dos
    instantes distintos con seis horas de diferencia, y la regla de horario
    habitual (R-05) depende de la hora. El portal formatea a hora de Costa Rica al
    presentar; la base guarda UTC.
    """
    if valor.tzinfo is None or valor.tzinfo.utcoffset(valor) is None:
        raise ValueError(
            "fecha sin zona horaria: usar datetime con tzinfo "
            "(por ejemplo datetime(2026, 9, 24, 22, 47, tzinfo=ZONA_COSTA_RICA))"
        )
    return valor.astimezone(UTC)


def asumir_utc(valor: datetime) -> datetime:
    """Version permisiva de `exigir_utc` para lo que llega de afuera.

    Una fecha sin zona se interpreta como UTC en vez de rechazarse. Se usa para los
    parametros de consulta de la API: es exactamente lo que hace pymongo con un
    datetime ingenuo, asi que el filtro se comporta igual en el repositorio real y en
    el doble de prueba. Para escribir documentos se usa `exigir_utc`, que no perdona.
    """
    if valor.tzinfo is None or valor.tzinfo.utcoffset(valor) is None:
        return valor.replace(tzinfo=UTC)
    return valor.astimezone(UTC)


MarcaTiempoUTC = Annotated[datetime, Field(description="ISODate en UTC")]

# Excepcion unica y declarada: indicadores_diarios.fecha es texto YYYY-MM-DD, para
# que sirva de _id natural de una coleccion materializada por dia.
FechaDia = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    Field(description="Fecha como texto YYYY-MM-DD; unica excepcion a ISODate"),
]

# ---------------------------------------------------------------------------
# Identificadores
# ---------------------------------------------------------------------------


def _identificador(prefijo: str, descripcion: str) -> object:
    """Identificador legible con prefijo, del CONTRATO-IDS.md.

    El patron es lo que impide que entre un ObjectId por descuido: un
    "507f1f77bcf86cd799439011" no pasa la validacion.
    """
    return Annotated[
        str,
        StringConstraints(pattern=rf"^{prefijo}-\d{{3,}}$"),
        Field(description=descripcion),
    ]


ClienteId = _identificador("CLI", "Cliente bancario, CLI-001")
CuentaId = _identificador("CTA", "Cuenta propia del banco, CTA-001")
TransaccionId = _identificador("TXN", "Transaccion, TXN-001")
ReglaId = _identificador("REG", "Regla de deteccion, REG-001")
RiesgoId = _identificador("RSK", "Entrada de lista de riesgo, RSK-001")
AgenteId = _identificador("AGT", "Agente de fraude, AGT-001")
AlertaId = _identificador("ALR", "Alerta, ALR-001")
CasoId = _identificador("CAS", "Caso de investigacion, CAS-001")
NotificacionId = _identificador("NOT", "Notificacion, NOT-001")

# Numero de cuenta SINPE, propio o externo (una cuenta mula no tiene documento en
# `cuentas`: viaja como este texto dentro de transacciones.cuenta_destino).
CuentaSinpe = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{4}$"),
    Field(description="Numero de cuenta SINPE, formato 8712-4455"),
]

Cedula = Annotated[
    str,
    StringConstraints(pattern=r"^\d-\d{4}-\d{4}$"),
    Field(description="Cedula de Costa Rica, formato 1-1111-1111"),
]


# ---------------------------------------------------------------------------
# Documento base
# ---------------------------------------------------------------------------


class ModeloCentinela(BaseModel):
    """Configuracion comun a todo modelo, embebido o de coleccion.

    `extra="ignore"` es deliberado y no es descuido. El esquema evoluciona: H-05
    agrega a `transacciones` una etiqueta oculta que solo sirve para medir y que el
    motor nunca lee, y H-08 va a agregar campos a `alertas`. Con `extra="forbid"`,
    leer un documento nuevo con codigo viejo explotaria, y el sistema tiene que
    poder leer las dos versiones durante una migracion (H-26).

    `populate_by_name=True` permite construir el modelo con el nombre del campo
    (`alerta_id=...`) o con su alias (`_id=...`). Al serializar con `by_alias=True`
    sale `_id`, que es lo que manda: el documento en Mongo.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    @field_validator("*", mode="after")
    @classmethod
    def normalizar_fechas(cls, valor: object) -> object:
        """Aplica la regla de UTC a todo campo datetime de todo modelo.

        Ponerlo aca y no en cada campo es a proposito: una fecha nueva en cualquier
        coleccion queda cubierta sin que nadie se acuerde de anotarla. Los modelos
        embebidos heredan de esta clase para que la regla tambien los cubra.
        """
        if isinstance(valor, datetime):
            return exigir_utc(valor)
        return valor


class DocumentoBase(ModeloCentinela):
    """Padre de los 11 modelos de dominio: todo documento lleva version de esquema."""

    esquema_version: int = Field(default=ESQUEMA_VERSION, ge=1)

    def a_documento(self) -> dict:
        """Documento listo para Mongo, con `_id` y no con el nombre del campo.

        No convierte el dinero a Int64: eso lo hace `app/repos/carga_demo.py` al
        escribir, porque es una decision de la capa que habla con la base.

        `exclude_none=True` no es cosmetico: los validadores `$jsonSchema` de las 11
        colecciones declaran `additionalProperties: false`, asi que un campo opcional
        escrito como `null` es una clave que el validador no conoce y la escritura se
        rechaza. Ausente y nulo no son lo mismo para Mongo.
        """
        return self.model_dump(by_alias=True, mode="python", exclude_none=True)
