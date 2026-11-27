"""Conjunto de demo: pequeno, reproducible y sin un solo dato inventado.

Todo lo que hay aqui sale de `scripts/practica1/CONTRATO-IDS.md` y del caso ya
documentado en `docs/02-casos-de-uso.md`: Maria Rodriguez, cuenta 8712-4455, TXN-001
de 280.000 colones a las 22:47 hacia la mula 6033-9001, alerta ALR-001 con puntaje 75
resuelta por el agente Jose Solis, y la cadena de mulas RSK-001..004 de UC-04.

Reproducible sin semilla aleatoria: son literales. Dos corridas de
`python tareas.py datos-demo` dejan la base identica, porque no hay nada que sortear.
Por eso este conjunto NO usa Faker: el generador con Faker es H-03/H-04/H-05 y es
otra cosa.

Vive en `pruebas/dobles/` porque es un doble de prueba que ademas sirve de demo, y la
direccion de las dependencias es pruebas -> app, nunca app -> pruebas. Se usa en dos
lugares: lo carga `tareas.py datos-demo` y lo consumen las pruebas de la API.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.modelos import (
    COLECCIONES,
    ESQUEMA_ALERTA_V1,
    ZONA_COSTA_RICA,
    AccionHistorial,
    Agente,
    Alerta,
    Canal,
    CanalNotificacion,
    Caso,
    Cliente,
    Cuenta,
    DocumentoBase,
    EntradaListaRiesgo,
    EstadoAlerta,
    EstadoCaso,
    EstadoEnvio,
    HorarioHabitual,
    IndicadorDiario,
    Notificacion,
    PerfilComportamiento,
    RolAgente,
    Severidad,
    Transaccion,
)

# La zona de presentacion se importa de app/modelos/base.py: una sola definicion
# para el portal, el canal SSE y el demo. Se escribe la hora local del caso de uso
# (22:47) y el modelo la normaliza a UTC al validar.

DIA = "2026-09-24"


def _local(hora: int, minuto: int, dia: int = 24) -> datetime:
    return datetime(2026, 9, dia, hora, minuto, tzinfo=ZONA_COSTA_RICA)


# ---------------------------------------------------------------------------
# Identidad
# ---------------------------------------------------------------------------

CLIENTES = [
    Cliente(
        _id="CLI-001",
        nombre="Maria Rodriguez Vargas",
        cedula="1-1111-1111",
        fecha_registro=_local(9, 0, dia=1),
        # T-82: Maria acepta el SMS de aviso; Luis tiene telefono pero no acepta; Ana no tiene.
        telefono="8712-4455",
        acepta_sms=True,
    ),
    Cliente(
        _id="CLI-002",
        nombre="Luis Fernandez Solano",
        cedula="2-2222-2222",
        fecha_registro=_local(9, 0, dia=2),
        telefono="6210-3387",
        acepta_sms=False,
    ),
    Cliente(
        _id="CLI-003",
        nombre="Ana Castro Jimenez",
        cedula="3-3333-3333",
        fecha_registro=_local(9, 0, dia=3),
    ),
    Cliente(
        _id="CLI-004",
        nombre="Carlos Mora Rojas",
        cedula="4-4444-4444",
        fecha_registro=_local(9, 0, dia=4),
        telefono="3390-6612",
        acepta_sms=True,
    ),
    # Titulares de las cuentas de la cadena de mulas del caso (UC-04). Son clientes del
    # banco: la red de mulas solo sigue el dinero entre cuentas con documento en `cuentas`.
    Cliente(
        _id="CLI-005",
        nombre="Titular cuenta mula 1",
        cedula="5-5555-5555",
        fecha_registro=_local(9, 0, dia=5),
    ),
    Cliente(
        _id="CLI-006",
        nombre="Titular cuenta mula 2",
        cedula="6-6666-6666",
        fecha_registro=_local(9, 0, dia=6),
    ),
    Cliente(
        _id="CLI-007",
        nombre="Titular cuenta mula 3",
        cedula="7-7777-7777",
        fecha_registro=_local(9, 0, dia=7),
    ),
    Cliente(
        _id="CLI-008",
        nombre="Titular cuenta mula 4",
        cedula="8-8888-8888",
        fecha_registro=_local(9, 0, dia=8),
    ),
]

# La entidad fija el tope SINPE Movil por transferencia (`generador.entidades` del YAML:
# A 300.000, B 200.000, C 150.000, D 100.000). Ningun monto del demo supera el tope de la
# entidad de su cuenta de origen; `prueba_demo_montos_bajo_el_tope` lo comprueba.
# Las cuatro ultimas son la cadena de mulas de UC-04, en el orden del dinero.
CUENTAS = [
    Cuenta(_id="CTA-001", cuenta_sinpe="8712-4455", cliente_id="CLI-001", entidad="Entidad A"),
    Cuenta(_id="CTA-002", cuenta_sinpe="6210-3387", cliente_id="CLI-002", entidad="Entidad B"),
    Cuenta(_id="CTA-003", cuenta_sinpe="5544-9021", cliente_id="CLI-003", entidad="Entidad C"),
    Cuenta(_id="CTA-004", cuenta_sinpe="3390-6612", cliente_id="CLI-004", entidad="Entidad D"),
    Cuenta(_id="CTA-005", cuenta_sinpe="6033-9001", cliente_id="CLI-005", entidad="Entidad A"),
    Cuenta(_id="CTA-006", cuenta_sinpe="7104-2288", cliente_id="CLI-006", entidad="Entidad B"),
    Cuenta(_id="CTA-007", cuenta_sinpe="8455-1177", cliente_id="CLI-007", entidad="Entidad C"),
    Cuenta(_id="CTA-008", cuenta_sinpe="6001-3344", cliente_id="CLI-008", entidad="Entidad D"),
]

# Los valores de CLI-001 son los ya documentados en docs/02: 47 transferencias,
# promedio 25.000, maximo 100.000, horario 07:00-19:00. No se agrega `desviacion_crc`
# porque el validador de `perfiles_comportamiento` todavia no la declara, y el
# documento en Mongo manda.
PERFILES = [
    PerfilComportamiento(
        _id="CLI-001",
        promedio_crc=25_000,
        maximo_crc=100_000,
        num_transferencias=47,
        horario_habitual=HorarioHabitual(inicio="07:00", fin="19:00"),
        destinos_frecuentes=["6210-3387", "5544-9021", "3390-6612"],
    ),
]

# ---------------------------------------------------------------------------
# Transaccional
# ---------------------------------------------------------------------------
#
# El catalogo de reglas NO esta aqui. Vive en `scripts/semilla/03-catalogos.js`, que
# es de H-07, y lo siembra `tareas.py datos-demo` antes de escribir estos documentos.
# Definirlo en los dos lugares es garantizar que se separen: los pesos de las reglas
# son de quien las disena, no de la plataforma.

# D-55: solo el primer salto esta reportado; las otras tres cuentas de la cadena son
# propuestas de la red de mulas y se marcan desde el Sankey.
LISTAS_RIESGO = [
    EntradaListaRiesgo(
        _id="RSK-001",
        cuenta="6033-9001",
        motivo="Primer salto de una cadena de fragmentacion",
        fecha_reporte=_local(23, 10),
    ),
]

# TXN-001 es la del caso de uso. Las demas son el contraste: montos chicos en horario
# de oficina hacia destinos habituales. La cadena de mulas de UC-04 va como
# TXN-005..007, con los montos y los tiempos exactos que documenta el caso.
TRANSACCIONES = [
    Transaccion(
        _id="TXN-001",
        cuenta_origen="CTA-001",
        sinpe_origen="8712-4455",
        cuenta_destino="6033-9001",
        monto_crc=280_000,
        canal=Canal.SINPE_MOVIL,
        fecha=_local(22, 47),
    ),
    Transaccion(
        _id="TXN-002",
        cuenta_origen="CTA-002",
        sinpe_origen="6210-3387",
        cuenta_destino="5544-9021",
        monto_crc=25_000,
        canal=Canal.SINPE_MOVIL,
        fecha=_local(10, 15),
    ),
    Transaccion(
        _id="TXN-003",
        cuenta_origen="CTA-003",
        sinpe_origen="5544-9021",
        cuenta_destino="3390-6612",
        monto_crc=48_300,
        canal=Canal.SINPE_MOVIL,
        fecha=_local(11, 40),
    ),
    Transaccion(
        _id="TXN-004",
        cuenta_origen="CTA-004",
        sinpe_origen="3390-6612",
        cuenta_destino="6210-3387",
        monto_crc=12_750,
        canal=Canal.SINPE_MOVIL,
        fecha=_local(16, 5),
    ),
    # La cadena de mulas de UC-04 (D-29): desde 6033-9001, que recibio TXN-001 a las 22:47,
    # el dinero sigue +4, +9 y +11 minutos despues, cada vez menos (276.000, 185.000 y
    # 90.000). Cada eslabon sale de la cuenta donde llego el anterior.
    Transaccion(
        _id="TXN-005",
        cuenta_origen="CTA-005",
        sinpe_origen="6033-9001",
        cuenta_destino="7104-2288",
        monto_crc=276_000,
        canal=Canal.SINPE_MOVIL,
        fecha=_local(22, 51),
    ),
    Transaccion(
        _id="TXN-006",
        cuenta_origen="CTA-006",
        sinpe_origen="7104-2288",
        cuenta_destino="8455-1177",
        monto_crc=185_000,
        canal=Canal.SINPE_MOVIL,
        fecha=_local(23, 0),
    ),
    Transaccion(
        _id="TXN-007",
        cuenta_origen="CTA-007",
        sinpe_origen="8455-1177",
        cuenta_destino="6001-3344",
        monto_crc=90_000,
        canal=Canal.SINPE_MOVIL,
        fecha=_local(23, 11),
    ),
]

# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------

AGENTES = [
    Agente(_id="AGT-001", nombre="Jose Solis", rol=RolAgente.AGENTE),
    Agente(_id="AGT-002", nombre="Laura Mendez", rol=RolAgente.AGENTE),
]

# Las tres alertas del demo son v1 (H-26): `reglas_disparadas` como lista de REG-xxx,
# igual que la ALR-001 sembrada por 01-colecciones.js. Son "lo que habia antes" y es lo
# que la migracion 001 convierte; la API las lee igual gracias a la lectura dual.
#
# ALR-001 es la del caso: puntaje 75 (35 + 25 + 15), severidad CRITICA, confirmada por
# AGT-001 a las 22:53 con la nota documentada. El historial muestra los dos pasos de
# la maquina de estados, porque `nueva -> confirmada` no existe: primero la toma.
ALERTAS = [
    Alerta(
        _id="ALR-001",
        esquema_version=ESQUEMA_ALERTA_V1,
        transaccion_id="TXN-001",
        reglas_disparadas=["REG-001", "REG-002", "REG-003"],
        puntaje=75,
        severidad=Severidad.CRITICA,
        estado=EstadoAlerta.CONFIRMADA,
        agente_id="AGT-001",
        fecha_creacion=_local(22, 47),
        # Copia de despliegue. La cuenta va como numero SINPE, que es lo que el
        # agente lee en el panel: CTA-001 es 8712-4455 (la cuenta de Maria).
        cuenta_origen="8712-4455",
        cuenta_destino="6033-9001",
        monto_crc=280_000,
        cliente_nombre="Maria Rodriguez Vargas",
        historial=[
            # La primera entrada es la creacion: la escribe el motor, sin agente.
            AccionHistorial(
                accion=EstadoAlerta.NUEVA,
                fecha=_local(22, 47),
                nota="Alerta creada por el motor de deteccion",
            ),
            AccionHistorial(
                accion=EstadoAlerta.EN_REVISION,
                agente_id="AGT-001",
                fecha=_local(22, 49),
                nota="Agente toma la alerta y llama al cliente",
            ),
            AccionHistorial(
                accion=EstadoAlerta.CONFIRMADA,
                agente_id="AGT-001",
                fecha=_local(22, 53),
                nota="Cliente confirma llamada previa",
            ),
        ],
    ),
    # Dos alertas mas, en otros estados y severidades, para que los filtros de
    # GET /api/v1/alertas se puedan probar de verdad y no contra un solo documento.
    Alerta(
        _id="ALR-002",
        esquema_version=ESQUEMA_ALERTA_V1,
        transaccion_id="TXN-003",
        reglas_disparadas=["REG-003"],
        puntaje=40,
        severidad=Severidad.MEDIA,
        estado=EstadoAlerta.NUEVA,
        fecha_creacion=_local(11, 41),
        cuenta_origen="5544-9021",
        cuenta_destino="3390-6612",
        monto_crc=48_300,
        cliente_nombre="Ana Castro Jimenez",
        historial=[
            AccionHistorial(
                accion=EstadoAlerta.NUEVA,
                fecha=_local(11, 41),
                nota="Alerta creada por el motor de deteccion",
            )
        ],
    ),
    Alerta(
        _id="ALR-003",
        esquema_version=ESQUEMA_ALERTA_V1,
        transaccion_id="TXN-004",
        reglas_disparadas=["REG-002", "REG-003"],
        puntaje=60,
        severidad=Severidad.ALTA,
        estado=EstadoAlerta.EN_REVISION,
        agente_id="AGT-002",
        fecha_creacion=_local(16, 6),
        cuenta_origen="3390-6612",
        cuenta_destino="6210-3387",
        monto_crc=12_750,
        cliente_nombre="Carlos Mora Rojas",
        historial=[
            AccionHistorial(
                accion=EstadoAlerta.NUEVA,
                fecha=_local(16, 6),
                nota="Alerta creada por el motor de deteccion",
            ),
            AccionHistorial(
                accion=EstadoAlerta.EN_REVISION,
                agente_id="AGT-002",
                fecha=_local(16, 20),
                nota="En contacto con el cliente",
            )
        ],
    ),
]

CASOS = [
    Caso(
        _id="CAS-001",
        alertas=["ALR-001"],
        estado=EstadoCaso.INVESTIGACION,
    ),
]

# ---------------------------------------------------------------------------
# Cierre
# ---------------------------------------------------------------------------

NOTIFICACIONES = [
    Notificacion(
        _id="NOT-001",
        alerta_id="ALR-001",
        canal=CanalNotificacion.CORREO,
        estado_envio=EstadoEnvio.ENVIADO,
        fecha=_local(22, 47, dia=24) + timedelta(seconds=30),
    ),
]

INDICADORES = [
    IndicadorDiario(
        _id=DIA,
        total_alertas=3,
        por_severidad={Severidad.CRITICA: 1, Severidad.ALTA: 1, Severidad.MEDIA: 1},
        por_estado={
            EstadoAlerta.CONFIRMADA: 1,
            EstadoAlerta.EN_REVISION: 1,
            EstadoAlerta.NUEVA: 1,
        },
    ),
]


# Mismo orden de insercion que el de las dependencias entre colecciones, para que la
# base quede coherente en cualquier momento de la carga.
DATOS_DEMO: dict[str, list[DocumentoBase]] = {
    "clientes": CLIENTES,
    "cuentas": CUENTAS,
    "perfiles_comportamiento": PERFILES,
    "listas_riesgo": LISTAS_RIESGO,
    "transacciones": TRANSACCIONES,
    "agentes": AGENTES,
    "alertas": ALERTAS,
    "casos": CASOS,
    "notificaciones": NOTIFICACIONES,
    "indicadores_diarios": INDICADORES,
}

# `reglas_deteccion` la siembra el script de catalogos de H-07, no este archivo.
COLECCIONES_DE_OTROS = frozenset({"reglas_deteccion"})

# Si alguien agrega una coleccion de dominio y se olvida del demo, esto lo grita al
# importar y no tres dias despues.
assert set(DATOS_DEMO) | COLECCIONES_DE_OTROS == set(COLECCIONES), (
    "el conjunto de demo tiene que cubrir las colecciones de dominio que le tocan: "
    f"faltan {set(COLECCIONES) - set(DATOS_DEMO) - COLECCIONES_DE_OTROS}, "
    f"sobran {set(DATOS_DEMO) - set(COLECCIONES)}"
)


def alertas_como_documentos() -> list[dict]:
    """Las alertas del demo como documentos, para el repo falso de las pruebas."""
    return [alerta.a_documento() for alerta in ALERTAS]


async def _cargar() -> None:
    """Entrada de `python tareas.py datos-demo`.

    El cableado con la base vive aca abajo y no en el nivel del modulo: importar
    este archivo desde una prueba no debe abrir una conexion a Mongo.
    """
    from app.nucleo.config import obtener_ajustes
    from app.nucleo.db import ciclo_cliente
    from app.repos.carga_demo import cargar_demo

    ajustes = obtener_ajustes()
    async with ciclo_cliente(ajustes) as cliente:
        base = cliente[ajustes.mongo.base]
        resumen = await cargar_demo(base, DATOS_DEMO)

    print(f"Base: {ajustes.mongo.base}")
    for coleccion, cantidad in resumen.items():
        print(f"  {coleccion}: {cantidad}")
    print(f"Total: {sum(resumen.values())} documentos en {len(resumen)} colecciones")


if __name__ == "__main__":
    asyncio.run(_cargar())
