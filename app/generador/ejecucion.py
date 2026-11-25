"""Arma el conjunto sintetico y lo escribe por lotes a ritmo configurable.

`generar_conjunto` es puro (sin Mongo, sin reloj): misma semilla y mismos ajustes dan el
mismo conjunto. `ejecutar` es la parte con I/O y solo habla con `RepoGenerador`.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import Counter
from dataclasses import dataclass, field

from app.generador.fraude import inyectar_fraude, presupuesto_de_fraude
from app.generador.poblacion import (
    Poblacion,
    construir_perfiles,
    documento_cliente,
    documento_cuenta,
    documento_perfil,
    generar_poblacion,
)
from app.generador.riesgo import documentos_lista_riesgo
from app.generador.transacciones import generar_legitimas, ordenar_y_numerar
from app.nucleo.config import AjustesGenerador
from app.repos.generador import RepoGenerador


@dataclass(frozen=True, slots=True)
class Conjunto:
    poblacion: Poblacion
    clientes: list[dict]
    cuentas: list[dict]
    perfiles: list[dict]
    transacciones: list[dict]
    listas_riesgo: list[dict]

    @property
    def fraude_por_patron(self) -> dict[str, int]:
        return dict(
            Counter(
                t["simulacion"]["patron"] for t in self.transacciones if t["simulacion"]["fraude"]
            )
        )

    @property
    def total_fraude(self) -> int:
        return sum(self.fraude_por_patron.values())


@dataclass(slots=True)
class Resumen:
    clientes: int = 0
    cuentas: int = 0
    perfiles: int = 0
    transacciones: int = 0
    listas_riesgo: int = 0
    fraude_por_patron: dict[str, int] = field(default_factory=dict)
    segundos: float = 0.0

    @property
    def total_fraude(self) -> int:
        return sum(self.fraude_por_patron.values())

    @property
    def documentos_por_segundo(self) -> float:
        return self.transacciones / self.segundos if self.segundos else 0.0


def generar_conjunto(ajustes: AjustesGenerador, *, primer_numero: int = 1) -> Conjunto:
    """Todo el conjunto en memoria, determinista respecto de `ajustes.semilla`.

    Cada etapa tiene su propio generador aleatorio derivado de la semilla, asi agregar
    o quitar una etapa no mueve los numeros de las otras. `primer_numero` solo desplaza la
    numeracion de las transacciones (TXN-NNNNNN); el contenido no cambia.
    """
    poblacion = generar_poblacion(ajustes, random.Random(f"{ajustes.semilla}:poblacion"))

    fraude_total = presupuesto_de_fraude(ajustes.transacciones, ajustes.proporcion_anomalos)
    legitimas = generar_legitimas(
        poblacion,
        ajustes,
        random.Random(f"{ajustes.semilla}:legitimas"),
        ajustes.transacciones - fraude_total,
    )
    perfiles = construir_perfiles(
        poblacion, ((t.cuenta_origen, t.cuenta_destino, t.monto_crc) for t in legitimas)
    )
    fraude = inyectar_fraude(
        poblacion, perfiles, ajustes, random.Random(f"{ajustes.semilla}:fraude"), fraude_total
    )

    transacciones = ordenar_y_numerar(
        legitimas + fraude,
        {cuenta.cuenta_id: cuenta.cuenta_sinpe for cuenta in poblacion.cuentas},
        primer_numero=primer_numero,
    )

    return Conjunto(
        poblacion=poblacion,
        clientes=[documento_cliente(c) for c in poblacion.clientes],
        cuentas=[documento_cuenta(c) for c in poblacion.cuentas],
        perfiles=[documento_perfil(p) for p in perfiles],
        transacciones=transacciones,
        listas_riesgo=documentos_lista_riesgo(
            poblacion,
            transacciones,
            ajustes.proporcion_mulas_reportadas,
            random.Random(f"{ajustes.semilla}:riesgo"),
        ),
    )


async def ejecutar(
    repo: RepoGenerador, ajustes: AjustesGenerador, *, limpiar: bool = True
) -> Resumen:
    """Genera y escribe. Con `ritmo_por_segundo` > 0 reparte la insercion en el tiempo."""
    resumen = Resumen()

    # Primero se limpia lo generado antes y despues se numera: asi continua despues del
    # mayor TXN de seis digitos que queda (la demo o lo insertado a mano), y lo nuevo es
    # siempre mayor que lo que D1 ya evaluo.
    if limpiar:
        await repo.limpiar_generado()
    conjunto = generar_conjunto(ajustes, primer_numero=await repo.siguiente_numero_transaccion())

    resumen.clientes = await repo.insertar_lote("clientes", conjunto.clientes)
    resumen.cuentas = await repo.insertar_lote("cuentas", conjunto.cuentas)
    resumen.perfiles = await repo.insertar_lote("perfiles_comportamiento", conjunto.perfiles)
    resumen.listas_riesgo = await repo.insertar_lote("listas_riesgo", conjunto.listas_riesgo)

    ritmo = ajustes.ritmo_por_segundo
    # Con ritmo, un lote nunca es mas grande que un segundo de insercion: asi el flujo se ve
    # llegar de a poco en vez de a golpes.
    lote = min(ajustes.tamano_lote, ritmo) if ritmo else ajustes.tamano_lote

    inicio = time.monotonic()
    documentos = conjunto.transacciones
    for desde in range(0, len(documentos), lote):
        resumen.transacciones += await repo.insertar_lote(
            "transacciones", documentos[desde : desde + lote]
        )
        if ritmo:
            adelanto = resumen.transacciones / ritmo - (time.monotonic() - inicio)
            if adelanto > 0:
                await asyncio.sleep(adelanto)
    resumen.segundos = time.monotonic() - inicio
    resumen.fraude_por_patron = await repo.fraude_por_patron()
    return resumen
