"""Alertas y trabajo del agente: agentes, alertas, casos.

Se corresponde con `scripts/practica1/c-alertas.js` (persona C).

`alertas` es la coleccion mas importante del proyecto: es la que el motor escribe,
la que el portal lee por SSE y la que tiene la clave de idempotencia
(`transaccion_id` con indice unico: una transaccion produce una sola alerta).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from app.modelos.base import (
    AgenteId,
    AlertaId,
    CasoId,
    CuentaSinpe,
    DocumentoBase,
    MarcaTiempoUTC,
    ModeloCentinela,
    Moneda,
    MontoCRC,
    ReglaId,
    TransaccionId,
)
from app.modelos.estados import ESTADO_INICIAL, EstadoAlerta, Severidad


class RolAgente(StrEnum):
    AGENTE = "agente"
    SUPERVISOR = "supervisor"
    ADMINISTRADOR = "administrador"


class ParametrosScrypt(ModeloCentinela):
    """Parametros con que se derivo el hash; viajan con la credencial (H-25)."""

    algoritmo: Literal["scrypt"] = "scrypt"
    n: int = Field(ge=2)
    r: int = Field(ge=1)
    p: int = Field(ge=1)
    dklen: int = Field(ge=16)


class Credencial(ModeloCentinela):
    """Contrasena cifrada de un agente: hash y sal en base64, nunca la contrasena."""

    hash: str = Field(min_length=1)
    sal: str = Field(min_length=1)
    parametros: ParametrosScrypt


class Agente(DocumentoBase):
    """Usuario humano del sistema.

    `credencial` es opcional: la semilla deja a todos sin ella y quien no la tiene no
    puede iniciar sesion. La crea `python -m app.seguridad.usuarios` (H-25).
    """

    agente_id: AgenteId = Field(alias="_id")
    nombre: str = Field(min_length=1)
    rol: RolAgente = RolAgente.AGENTE
    activo: bool = True
    credencial: Credencial | None = None


class AccionHistorial(ModeloCentinela):
    """Una entrada del historial embebido de la alerta.

    Embebido y no coleccion aparte porque se lee siempre junto con la alerta y nunca
    solo, y porque crece acotado (unas pocas acciones por alerta).

    H-14 AGREGA a esta lista, nunca la sobrescribe: es el rastro de auditoria.
    """

    accion: EstadoAlerta
    # Nulo cuando la accion fue automatica: la creacion la hace el motor, no una
    # persona. El validador de `alertas` lo declara ["string","null"].
    agente_id: AgenteId | None = None
    fecha: MarcaTiempoUTC
    nota: str = ""


# ---------------------------------------------------------------------------
# Versiones de esquema de la alerta (H-26)
# ---------------------------------------------------------------------------
#
#   v1  reglas_disparadas = ["REG-001", ...]; la version de cada regla y la politica
#       quedaban solo como texto en historial[0].nota (lo que escribia D1 hasta H-26).
#   v2  reglas_disparadas = [{regla_id, codigo, version, peso, explicacion}, ...] y
#       politica = {politica_id, version}: la alerta se explica sola dentro de un ano.
#
# D1 escribe v2. Las v1 se convierten con scripts/migraciones/001-alertas-v1-a-v2.js;
# mientras tanto el sistema LEE LAS DOS (lectura dual, por `esquema_version`).

ESQUEMA_ALERTA_V1 = 1
ESQUEMA_ALERTA_V2 = 2
# La version que escribe este codigo. Las otras colecciones siguen en ESQUEMA_VERSION.
ESQUEMA_ALERTA_VIGENTE = ESQUEMA_ALERTA_V2

PoliticaId = Annotated[
    str,
    StringConstraints(pattern=r"^POL-\d{3,}$"),
    Field(description="Politica de deteccion, POL-001"),
]


class ReglaAplicada(ModeloCentinela):
    """Copia embebida de una regla que disparo, con la version con que se evaluo (v2).

    `peso` es el aporte al puntaje antes del tope de la politica; `explicacion` es la
    del evaluador (H-08) con el valor observado contra el umbral. Si la escribio la
    migracion, la explicacion se reconstruye desde la regla en esa version.
    """

    regla_id: ReglaId
    codigo: str = Field(pattern=r"^R-\d{2}$")
    version: int = Field(ge=1)
    peso: int = Field(ge=0, le=100)
    explicacion: str = Field(min_length=1)


class PoliticaAplicada(ModeloCentinela):
    """La politica con que se calculo la severidad (v2)."""

    politica_id: PoliticaId
    version: int = Field(ge=1)


class Alerta(DocumentoBase):
    """Alerta de posible fraude, con el motivo por el que nacio adentro.

    Lectura dual (H-26): el mismo modelo lee v1 y v2, y `esquema_version` decide que
    forma de `reglas_disparadas` es valida. Una mezcla (v2 con lista de cadenas, v1
    con politica) se rechaza aqui y en el validador `$jsonSchema`. Un solo modelo y no
    una union discriminada a proposito: la API, el canal SSE y el portal siguen
    leyendo `Alerta.model_validate(documento)` durante la transicion sin ramificar.

    Para escribir, `esquema_version` vale 2 por omision: es lo que escribe D1.
    """

    alerta_id: AlertaId = Field(alias="_id")
    esquema_version: Literal[1, 2] = ESQUEMA_ALERTA_VIGENTE
    # Clave de idempotencia. El indice unico sobre este campo es lo que garantiza que
    # reiniciar el motor (H-09) no duplique alertas.
    transaccion_id: TransaccionId
    # v1: identificadores. v2: copia embebida con version, peso y explicacion.
    reglas_disparadas: list[ReglaAplicada] | list[ReglaId] = Field(min_length=1)
    # Solo v2: la politica con que se evaluo. En v1 esa informacion vivia en la nota.
    politica: PoliticaAplicada | None = None
    puntaje: int = Field(ge=0, le=100)
    severidad: Severidad
    estado: EstadoAlerta = ESTADO_INICIAL
    # Nula mientras la alerta esta `nueva`: todavia no la tomo nadie.
    agente_id: AgenteId | None = None
    # Necesaria para el filtro por rango de fecha del panel (H-13) y para el orden
    # por defecto de GET /api/v1/alertas.
    fecha_creacion: MarcaTiempoUTC
    # Al menos una entrada SIEMPRE: la primera es la creacion, que escribe el motor
    # con `agente_id` nulo porque no la hizo una persona. Una alerta sin historial no
    # podria decir cuando nacio ni quien la toco, que es justo lo que el rastro de
    # auditoria tiene que responder. H-14 agrega con $push; nunca se reescribe.
    historial: list[AccionHistorial] = Field(min_length=1)

    # --- Copia de los campos de despliegue, al momento de crear la alerta ---
    #
    # La fila del panel necesita cliente, cuentas y monto para que el agente decida
    # sin abrir el detalle. Se copian en vez de resolverse con $lookup en cada pagina,
    # por dos razones que ya estaban decididas:
    #
    #  - "Nada se recalcula dentro de una peticion HTTP" (docs/04, reglas de oro).
    #  - Es el mismo principio por el que las reglas disparadas van embebidas: la
    #    alerta tiene que poder explicarse dentro de un ano, y para eso el monto que
    #    se vio al crearla no puede depender de que la transaccion siga igual.
    #
    # OJO: aca `cuenta_origen` es el NUMERO SINPE (8712-4455), no el identificador
    # CTA-001 como en `transacciones`. Es deliberado y es lo que declara el validador.
    cuenta_origen: CuentaSinpe
    cuenta_destino: CuentaSinpe
    monto_crc: MontoCRC
    moneda: Moneda = "CRC"
    cliente_nombre: str = Field(min_length=1)

    @model_validator(mode="after")
    def _forma_segun_version(self) -> Self:
        embebidas = [r for r in self.reglas_disparadas if isinstance(r, ReglaAplicada)]
        if self.esquema_version == ESQUEMA_ALERTA_V1:
            if embebidas or self.politica is not None:
                raise ValueError(
                    "esquema_version 1: reglas_disparadas es una lista de REG-xxx y no "
                    "lleva politica; la copia embebida es esquema_version 2"
                )
        elif len(embebidas) != len(self.reglas_disparadas) or self.politica is None:
            raise ValueError(
                "esquema_version 2: reglas_disparadas lleva {regla_id, codigo, version, "
                "peso, explicacion} y la alerta lleva politica {politica_id, version}"
            )
        return self

    @property
    def regla_ids(self) -> list[str]:
        """Los REG-xxx que dispararon, igual en v1 y en v2."""
        return [
            r.regla_id if isinstance(r, ReglaAplicada) else r for r in self.reglas_disparadas
        ]

    @property
    def reglas_aplicadas(self) -> list[ReglaAplicada]:
        """La copia embebida (v2). Vacia en v1: falta correr la migracion 001."""
        return [r for r in self.reglas_disparadas if isinstance(r, ReglaAplicada)]


class EstadoCaso(StrEnum):
    INVESTIGACION = "investigacion"
    CERRADO = "cerrado"


class Caso(DocumentoBase):
    """Escalacion de una o varias alertas a una investigacion.

    Escalar crea el caso y mueve la alerta en la misma transaccion multi-documento
    (H-14): por eso el replica set no es opcional.
    """

    caso_id: CasoId = Field(alias="_id")
    alertas: list[AlertaId] = Field(min_length=1)
    estado: EstadoCaso = EstadoCaso.INVESTIGACION
    # Opcional porque el validador de `casos` todavia no la declara.
    fecha_apertura: MarcaTiempoUTC | None = None
