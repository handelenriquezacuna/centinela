"""Lo que el portal agrega a un caso (T-56): la sugerencia y las acciones del lote.

El motor sugiere, el humano decide. `sugerir_decision` es una funcion pura que devuelve
un texto para mostrar; nada aqui cierra ni mueve una alerta. Los umbrales son de
presentacion (cuando vale la pena resaltar un caso), no cortes de severidad: esos viven
en `politica_deteccion`.
"""

from __future__ import annotations

from datetime import timedelta
from math import ceil

from app.modelos import EstadoAlerta, transiciones_validas
from app.repos.casos_destino import CasoDestino, ProtocoloRepoCasosDestino

VICTIMAS_PARA_PATRON = 3
VENTANA_PATRON = timedelta(minutes=20)

# Orden y rotulo de las acciones del lote: (destino, rotulo, clase del boton).
ACCIONES_LOTE = [
    (EstadoAlerta.EN_REVISION, "Tomar", "boton-primario"),
    (EstadoAlerta.CONFIRMADA, "Confirmar fraude", "boton-confirmar"),
    (EstadoAlerta.DESCARTADA, "Descartar", "boton-descartar"),
    (EstadoAlerta.ESCALADA, "Escalar", "boton-escalar"),
]


def sugerir_decision(caso: CasoDestino) -> str | None:
    """Texto de ayuda para el agente, o `None` si el caso no tiene nada que resaltar."""
    ventana = caso.mas_reciente - caso.mas_antigua
    en_patron = caso.victimas >= VICTIMAS_PARA_PATRON and ventana <= VENTANA_PATRON
    if en_patron and caso.en_lista_riesgo:
        minutos = max(1, ceil(ventana.total_seconds() / 60))
        return (
            f"{caso.victimas} víctimas en {minutos} min y la cuenta ya está reportada: "
            "candidata a confirmar."
        )
    if en_patron:
        minutos = max(1, ceil(ventana.total_seconds() / 60))
        return f"{caso.victimas} víctimas en {minutos} min hacia la misma cuenta: candidata a confirmar o escalar."
    if caso.en_lista_riesgo and caso.victimas >= 2:
        return f"{caso.victimas} víctimas y la cuenta ya está reportada: candidata a confirmar."
    if caso.en_lista_riesgo:
        return "La cuenta ya está reportada en listas de riesgo: revisar primero."
    return None


def acciones_del_caso(caso: CasoDestino) -> list[tuple[str, str, str]]:
    """Las acciones que la maquina de estados permite para al menos una alerta del caso."""
    posibles: set[EstadoAlerta] = set()
    for alerta in caso.alertas:
        posibles |= transiciones_validas(EstadoAlerta(alerta["estado"]))
    return [(d.value, rotulo, clase) for d, rotulo, clase in ACCIONES_LOTE if d in posibles]


def con_sugerencia(caso: CasoDestino) -> CasoDestino:
    caso.sugerencia = sugerir_decision(caso)
    return caso


async def casos_para_panel(
    repo: ProtocoloRepoCasosDestino, limite: int
) -> tuple[list[CasoDestino], int]:
    """Los casos de la pagina, con su sugerencia, y el total que hay."""
    casos, total = await repo.listar(limite=limite)
    return [con_sugerencia(c) for c in casos], total
