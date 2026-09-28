"""Los 11 modelos de dominio de Centinela y el mapa nombre de coleccion -> modelo.

Los archivos estan agrupados igual que los scripts de la Practica 1, para que cada
persona encuentre sus modelos donde tiene su script:

    identidad.py     <- scripts/practica1/a-identidad.js
    transaccional.py <- scripts/practica1/b-transaccional.js
    alertas.py       <- scripts/practica1/c-alertas.js
    cierre.py        <- scripts/practica1/d-cierre.js

Las 13 colecciones del sistema son estas 11 de dominio mas dos de operacion que
todavia no existen y que NO se modelan aqui: `politica_deteccion` (los cortes de
severidad, que por eso no estan en el YAML) y `deteccion_estado` (el resumeToken
del change stream de H-09). Se nombran en docs/06-plataforma.md para que nadie las
invente con otro nombre.
"""

from __future__ import annotations

from app.modelos.alertas import (
    AccionHistorial,
    Agente,
    Alerta,
    Caso,
    EstadoCaso,
    RolAgente,
)
from app.modelos.base import (
    ESQUEMA_VERSION,
    ZONA_COSTA_RICA,
    DocumentoBase,
    FechaDia,
    MarcaTiempoUTC,
    ModeloCentinela,
    MontoCRC,
)
from app.modelos.cierre import (
    CanalNotificacion,
    EstadoEnvio,
    IndicadorDiario,
    Notificacion,
)
from app.modelos.estados import (
    ESTADO_INICIAL,
    ESTADOS_ABIERTOS,
    ESTADOS_TERMINALES,
    TRANSICIONES,
    EstadoAlerta,
    Severidad,
    TransicionInvalida,
    exigir_transicion,
    transicion_permitida,
    transiciones_validas,
)
from app.modelos.identidad import (
    Cliente,
    Cuenta,
    HorarioHabitual,
    PerfilComportamiento,
)
from app.modelos.transaccional import (
    Canal,
    EntradaListaRiesgo,
    Regla,
    Transaccion,
)

# El mapa unico de nombres de coleccion. Cualquier codigo que necesite el nombre de
# una coleccion lo toma de aqui y no lo escribe a mano: asi un nombre mal escrito es
# un error de importacion y no una coleccion vacia que aparece de la nada.
COLECCIONES: dict[str, type[DocumentoBase]] = {
    "clientes": Cliente,
    "cuentas": Cuenta,
    "perfiles_comportamiento": PerfilComportamiento,
    "transacciones": Transaccion,
    "reglas_deteccion": Regla,
    "listas_riesgo": EntradaListaRiesgo,
    "agentes": Agente,
    "alertas": Alerta,
    "casos": Caso,
    "notificaciones": Notificacion,
    "indicadores_diarios": IndicadorDiario,
}

COLECCION_ALERTAS = "alertas"
COLECCION_TRANSACCIONES = "transacciones"

# Colecciones de operacion, todavia sin modelo (H-00B y H-09).
COLECCION_POLITICA = "politica_deteccion"
COLECCION_DETECCION_ESTADO = "deteccion_estado"

__all__ = [
    "COLECCIONES",
    "COLECCION_ALERTAS",
    "COLECCION_DETECCION_ESTADO",
    "COLECCION_POLITICA",
    "COLECCION_TRANSACCIONES",
    "ESQUEMA_VERSION",
    "ESTADOS_ABIERTOS",
    "ESTADOS_TERMINALES",
    "ESTADO_INICIAL",
    "TRANSICIONES",
    "ZONA_COSTA_RICA",
    "AccionHistorial",
    "Agente",
    "Alerta",
    "Canal",
    "CanalNotificacion",
    "Caso",
    "Cliente",
    "Cuenta",
    "DocumentoBase",
    "EntradaListaRiesgo",
    "EstadoAlerta",
    "EstadoCaso",
    "EstadoEnvio",
    "FechaDia",
    "HorarioHabitual",
    "IndicadorDiario",
    "MarcaTiempoUTC",
    "ModeloCentinela",
    "MontoCRC",
    "Notificacion",
    "PerfilComportamiento",
    "Regla",
    "RolAgente",
    "Severidad",
    "Transaccion",
    "TransicionInvalida",
    "exigir_transicion",
    "transicion_permitida",
    "transiciones_validas",
]
