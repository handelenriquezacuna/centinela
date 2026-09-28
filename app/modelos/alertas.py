"""Alertas y trabajo del agente: agentes, alertas, casos.

Se corresponde con `scripts/practica1/c-alertas.js` (persona C).

`alertas` es la coleccion mas importante del proyecto: es la que el motor escribe,
la que el portal lee por SSE y la que tiene la clave de idempotencia
(`transaccion_id` con indice unico: una transaccion produce una sola alerta).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

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


class Agente(DocumentoBase):
    """Usuario humano del sistema. La autenticacion es H-25; aca solo la identidad."""

    agente_id: AgenteId = Field(alias="_id")
    nombre: str = Field(min_length=1)
    rol: RolAgente = RolAgente.AGENTE
    activo: bool = True


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


class Alerta(DocumentoBase):
    """Alerta de posible fraude, con el motivo por el que nacio adentro.

    Sobre `reglas_disparadas`: hoy es la lista de identificadores de regla, tal como
    la fija `scripts/practica1/CONTRATO-IDS.md`. En H-08 evoluciona a copia embebida
    con version y peso aplicado, subiendo `esquema_version` de 1 a 2; esa es la
    migracion real que H-26 demuestra el 19 de octubre. Mientras tanto, el detalle
    del aporte de cada regla se reconstruye leyendo `reglas_deteccion`.
    """

    alerta_id: AlertaId = Field(alias="_id")
    # Clave de idempotencia. El indice unico sobre este campo es lo que garantiza que
    # reiniciar el motor (H-09) no duplique alertas.
    transaccion_id: TransaccionId
    reglas_disparadas: list[ReglaId] = Field(min_length=1)
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
    # auditoria tiene que responder.
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
    # A D1 no le cuesta una consulta extra: cuando escribe la alerta ya tiene la
    # transaccion en la mano, porque llego en el evento del change stream.
    # OJO: aca `cuenta_origen` es el NUMERO SINPE (8712-4455), no el identificador
    # CTA-001 como en `transacciones`. Es deliberado y es lo que declara el validador:
    # un campo de despliegue existe para mostrarse, y el agente lee el numero de
    # cuenta, no una llave interna. El mismo nombre de campo significa cosas distintas
    # en las dos colecciones, asi que esta anotado en docs/06-plataforma.md.
    cuenta_origen: CuentaSinpe
    cuenta_destino: CuentaSinpe
    monto_crc: MontoCRC
    moneda: Moneda = "CRC"
    cliente_nombre: str = Field(min_length=1)


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
