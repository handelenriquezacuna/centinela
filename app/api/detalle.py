"""El detalle de una alerta explicado (H-18): logica pura que comparten el portal y la API.

La pagina HTMX `/detalle/{alerta_id}` y `GET /api/v1/alertas/{alerta_id}/detalle` (T-61)
muestran lo mismo: cada regla disparada con su aporte y su evidencia, los cortes de la
politica con que se calculo la severidad, el historial y las acciones que permite la
maquina de estados. Todo eso se calcula aqui una sola vez; ni la plantilla ni la SPA lo
recalculan. No hay consultas: el caso de una alerta escalada se pide al repositorio.
"""

from __future__ import annotations

import re
from typing import Any

from app.api.formatos import colones, decimal
from app.modelos import ZONA_COSTA_RICA, EstadoAlerta, transiciones_validas
from app.repos.detalle import ProtocoloRepoDetalle

# Orden y rotulo de los botones. Cuales aparecen lo decide la maquina de estados.
# Cuarto elemento: ayuda corta del boton. Quinto: si la accion cierra la alerta y pide confirmar.
ACCIONES = [
    (
        EstadoAlerta.EN_REVISION,
        "Tomar",
        "boton-primario",
        "Te asigna la alerta y la pasa a En revisión.",
        False,
    ),
    (
        EstadoAlerta.CONFIRMADA,
        "Confirmar fraude",
        "boton-confirmar",
        "Cierra la alerta como fraude real. No se puede deshacer.",
        True,
    ),
    (
        EstadoAlerta.DESCARTADA,
        "Descartar",
        "boton-descartar",
        "Cierra la alerta como falso positivo. No se puede deshacer.",
        True,
    ),
    (
        EstadoAlerta.ESCALADA,
        "Escalar a caso",
        "boton-escalar",
        "Abre un caso de investigación y cierra la alerta. No se puede deshacer.",
        True,
    ),
]

AVISO_V1 = "alerta v1: migrar con python tareas.py migrar"


def porcentaje(valor: int, total: int) -> int:
    return max(0, min(100, round(valor * 100 / total))) if total > 0 else 0


_PREFIJO_REGLA = re.compile(r"^\s*R-\d+\s+v\d+\s*:\s*")
_APORTE_INTERNO = re.compile(r"\s*\(aporta\s+\d+[^)]*\)")
_RUIDO_MIGRACION = re.compile(r";?\s*explicaci[oó]n reconstruida por la migraci[oó]n 001", re.IGNORECASE)


def limpiar_explicacion(texto: str) -> str:
    """Quita lo que la tabla ya muestra en otra columna: "R-01 v2:", "(aporta 35)" y la nota de migracion."""
    limpio = _PREFIJO_REGLA.sub("", texto)
    limpio = _APORTE_INTERNO.sub("", limpio)
    limpio = _RUIDO_MIGRACION.sub("", limpio).strip(" ;")
    return limpio[:1].upper() + limpio[1:]


def evidencia(
    tipo: str | None,
    alerta: dict[str, Any],
    transaccion: dict[str, Any] | None,
    perfil: dict[str, Any] | None,
) -> str | None:
    """Una linea calculada con datos que la pagina ya muestra. None si no alcanza para calcularla."""
    if perfil is None:
        return None
    if tipo == "monto":
        promedio = perfil.get("promedio_crc")
        if isinstance(promedio, int) and promedio > 0:
            veces = decimal(alerta["monto_crc"] / promedio)
            return (
                f"Monto {colones(alerta['monto_crc'])} contra un promedio de "
                f"{colones(promedio)}: {veces}x."
            )
    if tipo == "destino":
        frecuentes = [str(c) for c in perfil.get("destinos_frecuentes") or []]
        destino = alerta["cuenta_destino"]
        if destino not in frecuentes:
            return (
                f"El destino {destino} no figura entre los {len(frecuentes)} destinos "
                "frecuentes del cliente."
            )
    if tipo == "horario":
        horario = perfil.get("horario_habitual") or {}
        momento = (transaccion or {}).get("fecha") or alerta["fecha_creacion"]
        if horario.get("inicio") and horario.get("fin"):
            local = momento.astimezone(ZONA_COSTA_RICA)
            if not horario["inicio"] <= f"{local:%H:%M}" <= horario["fin"]:
                return (
                    f"{local:%H:%M} (hora CR) está fuera del horario habitual "
                    f"{horario['inicio']}-{horario['fin']}."
                )
    return None


def cortes_de(
    alerta: dict[str, Any], vigente: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, str]:
    """La politica con que se calculo la alerta, y de donde salieron los cortes.

    Devuelve `(documento, origen)`: `usada` si es la politica que la alerta registra (la
    vigente o una version anterior de su historia); `vigente` si la alerta no registra
    politica o su version ya no esta disponible (los cortes son los de ahora, y la
    pagina lo rotula); `ninguna` si no hay politica en la base.
    """
    if vigente is None:
        return None, "ninguna"
    usada = alerta.get("politica")
    if not usada:
        return vigente, "vigente"
    if usada["politica_id"] == vigente.get("_id") and usada["version"] == vigente.get("version"):
        return vigente, "usada"
    if usada["politica_id"] == vigente.get("_id"):
        for anterior in vigente.get("versiones_anteriores") or []:
            if anterior.get("version") == usada["version"] and anterior.get("cortes_severidad"):
                return anterior, "usada"
    return vigente, "vigente"


def explicacion_puntaje(
    alerta: dict[str, Any],
    politica: dict[str, Any] | None,
    *,
    tipos: dict[str, str],
    transaccion: dict[str, Any] | None,
    perfil: dict[str, Any] | None,
) -> dict[str, Any]:
    """Los aportes en orden, el acumulado de cada uno y el corte que alcanza el puntaje.

    Solo v2 trae aporte por regla; en v1 la lista es de ids y no hay nada que sumar.
    `politica` es el documento cuyos cortes se muestran (ver `cortes_de`).
    """
    puntaje = alerta["puntaje"]
    maximo = politica["puntaje_maximo"] if politica is not None else 100
    acumulado = 0
    filas = []
    for regla in alerta["reglas_disparadas"]:
        if not isinstance(regla, dict):
            continue
        acumulado += regla["peso"]
        filas.append(
            {
                **regla,
                "explicacion": limpiar_explicacion(regla["explicacion"]),
                "evidencia": evidencia(tipos.get(regla["regla_id"]), alerta, transaccion, perfil),
                "acumulado": acumulado,
                "ancho": porcentaje(regla["peso"], maximo),
            }
        )
    cortes = []
    if politica is not None:
        for corte in politica["cortes_severidad"]:
            cortes.append(
                {
                    "severidad": str(corte["severidad"]),
                    "minimo": corte["puntaje_minimo"],
                    "maximo": corte["puntaje_maximo"],
                    "inicio": porcentaje(corte["puntaje_minimo"], maximo),
                    "alcanzado": corte["puntaje_minimo"] <= puntaje <= corte["puntaje_maximo"],
                }
            )
    return {
        "filas": filas,
        "suma": acumulado,
        "tope": acumulado > puntaje,
        "maximo": maximo,
        "cortes": cortes,
        "ancho_puntaje": porcentaje(puntaje, maximo),
        "umbral": politica["umbral_alerta"] if politica is not None else None,
    }


def es_v2(alerta: dict[str, Any]) -> bool:
    reglas = alerta["reglas_disparadas"]
    return bool(reglas) and all(isinstance(r, dict) for r in reglas)


def acciones_permitidas(estado: str) -> list[tuple[str, str, str, str, bool]]:
    """(destino, rotulo, clase, ayuda, cierra) de cada accion que la maquina permite."""
    permitidas = transiciones_validas(EstadoAlerta(estado))
    return [
        (e.value, rotulo, clase, ayuda, terminal)
        for e, rotulo, clase, ayuda, terminal in ACCIONES
        if e in permitidas
    ]


async def caso_de(alerta: dict[str, Any], repo_detalle: ProtocoloRepoDetalle) -> str | None:
    """El `CAS-xxx` de una alerta escalada; `None` en cualquier otro estado."""
    if alerta["estado"] != EstadoAlerta.ESCALADA.value:
        return None
    caso = await repo_detalle.caso_de_alerta(alerta["_id"])
    return caso["_id"] if caso is not None else None
