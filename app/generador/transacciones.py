"""H-04 · Transacciones legitimas y ensamblado del flujo final.

El flujo legitimo respeta el perfil de cada cliente: montos alrededor de su promedio
(nunca arriba de 5 veces, y nunca arriba del tope de su entidad), horas dentro de su
horario habitual con la forma de un dia real (picos al mediodia y a la tarde), y
destinos casi siempre entre sus frecuentes.

`TxnBorrador` es la transaccion antes de tener identificador. Los identificadores
(`TXN-000001`...) se asignan al final, en orden cronologico, para que el flujo se
inserte en el orden en que ocurrio y el detector vea la historia como la veria en vivo.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.generador.poblacion import (
    MONTO_MINIMO_CRC,
    HabitosGen,
    Poblacion,
    redondear_monto,
)
from app.modelos.base import ESQUEMA_VERSION, ZONA_COSTA_RICA
from app.nucleo.config import AjustesGenerador
from app.repos.ids_transaccion import id_transaccion

# Peso relativo de cada hora local (0 a 23) de un dia de transferencias SINPE Movil:
# valle de madrugada, subida a media manana, pico al mediodia y otro a la salida.
PESO_POR_HORA = (
    0.2, 0.1, 0.1, 0.1, 0.1, 0.3, 0.8, 1.5, 2.2, 2.6, 2.8, 3.0,
    3.4, 3.2, 2.8, 2.6, 2.8, 3.2, 3.4, 3.0, 2.2, 1.4, 0.8, 0.4,
)  # fmt: skip

# Fraccion de las transacciones legitimas que cae fuera del horario habitual (un cliente
# real a veces paga algo de noche) y fraccion que va a un destino que no es frecuente.
FRACCION_FUERA_DE_HORARIO = 0.03
FRACCION_DESTINO_OCASIONAL = 0.08
# Minimo de transacciones legitimas por cliente, para que todo cliente tenga perfil.
MINIMO_POR_CLIENTE = 5


@dataclass(frozen=True, slots=True)
class TxnBorrador:
    cuenta_origen: str
    cuenta_destino: str
    monto_crc: int
    fecha: datetime
    fraude: bool = False
    patron: str | None = None
    episodio: str | None = None


def limite_de_la_historia(ajustes: AjustesGenerador) -> datetime:
    """Instante UTC (exclusivo) en que termina la historia: medianoche local del dia
    siguiente a `fecha_fin`. Es fijo: el reloj de la maquina no interviene."""
    fin_local = datetime(
        ajustes.fecha_fin.year, ajustes.fecha_fin.month, ajustes.fecha_fin.day,
        tzinfo=ZONA_COSTA_RICA,
    ) + timedelta(days=1)  # fmt: skip
    return fin_local.astimezone(UTC)


def instante(
    ajustes: AjustesGenerador, dia: int, hora_local: int, rng: random.Random
) -> datetime:
    """Fecha UTC del dia `dia` (0 = el primero de la historia) a esa hora local."""
    inicio_local = limite_de_la_historia(ajustes).astimezone(ZONA_COSTA_RICA) - timedelta(
        days=ajustes.dias_historia
    )
    local = inicio_local + timedelta(
        days=dia, hours=hora_local, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
    )
    return local.astimezone(UTC)


def elegir_hora(
    habitos: HabitosGen, rng: random.Random, *, fuera_del_horario: bool
) -> int:
    """Hora local con la forma del dia, dentro o fuera del horario habitual."""
    dentro = set(habitos.horas_habituales())
    candidatas = [h for h in range(24) if (h not in dentro) == fuera_del_horario]
    if not candidatas:  # horario de 24 h: no hay "fuera"
        candidatas = list(range(24))
    return rng.choices(candidatas, weights=[PESO_POR_HORA[h] for h in candidatas], k=1)[0]


def _monto_legitimo(habitos: HabitosGen, rng: random.Random) -> int:
    """Lognormal alrededor del promedio del cliente, con piso y con techo.

    El techo (5 veces el promedio, y nunca mas que el tope de la entidad) es lo que
    garantiza que lo legitimo no se parezca a un monto atipico.
    """
    factor = rng.lognormvariate(-0.15, 0.6)
    monto = redondear_monto(habitos.promedio_objetivo_crc * factor)
    return max(MONTO_MINIMO_CRC, min(monto, habitos.maximo_objetivo_crc))


def generar_legitimas(
    poblacion: Poblacion, ajustes: AjustesGenerador, rng: random.Random, cantidad: int
) -> list[TxnBorrador]:
    """`cantidad` transacciones legitimas repartidas segun la actividad de cada cliente."""
    habitos = poblacion.habitos
    cuentas = {cuenta.cliente_id: cuenta for cuenta in poblacion.cuentas}
    if cantidad < MINIMO_POR_CLIENTE * len(habitos):
        raise ValueError(
            f"{cantidad} transacciones legitimas no alcanzan para {len(habitos)} clientes "
            f"(minimo {MINIMO_POR_CLIENTE} por cliente)"
        )

    # Cada cliente recibe su minimo, y el resto se reparte por actividad.
    asignados = [h for h in habitos for _ in range(MINIMO_POR_CLIENTE)]
    restantes = cantidad - len(asignados)
    asignados += rng.choices(habitos, weights=[h.actividad for h in habitos], k=restantes)

    resultado: list[TxnBorrador] = []
    for habito in asignados:
        cuenta = cuentas[habito.cliente_id]
        fuera = rng.random() < FRACCION_FUERA_DE_HORARIO
        hora = elegir_hora(habito, rng, fuera_del_horario=fuera)
        fecha = instante(ajustes, rng.randrange(ajustes.dias_historia), hora, rng)

        if rng.random() < FRACCION_DESTINO_OCASIONAL:
            destino = rng.choice(poblacion.destinos_externos)
        else:
            # Los primeros destinos de la lista pesan mas: hay un destino "de siempre".
            pesos = [1 / (posicion + 1) for posicion in range(len(habito.destinos_frecuentes))]
            destino = rng.choices(habito.destinos_frecuentes, weights=pesos, k=1)[0]

        resultado.append(
            TxnBorrador(
                cuenta_origen=cuenta.cuenta_id,
                cuenta_destino=destino,
                monto_crc=_monto_legitimo(habito, rng),
                fecha=fecha,
            )
        )
    return resultado


def ordenar_y_numerar(
    borradores: list[TxnBorrador], sinpe_de: Mapping[str, str], *, primer_numero: int = 1
) -> list[dict]:
    """Documentos de `transacciones`, en orden cronologico y con su TXN-xxxxxx.

    El orden total (fecha y luego los demas campos) hace que el resultado no dependa de
    como se construyo la lista: misma semilla, mismo contenido. `primer_numero` solo
    desplaza la numeracion: el generador continua despues del mayor TXN de seis digitos
    que ya existe (`app/repos/ids_transaccion.py`), para que lo nuevo sea siempre mayor
    que lo ya evaluado y el rezago de D1 cuente bien.

    `sinpe_de` es `cuenta_id -> cuenta_sinpe`: cada transaccion lleva el SINPE de su
    origen (`sinpe_origen`), el mismo espacio que `cuenta_destino`, para que la red de
    mulas (H-23) se recorra con un `$graphLookup`. Una cuenta de origen sin SINPE es un
    error del generador y revienta aqui (KeyError), no en la base.
    """
    ordenados = sorted(
        borradores,
        key=lambda t: (t.fecha, t.cuenta_origen, t.cuenta_destino, t.monto_crc),
    )
    documentos: list[dict] = []
    for numero, txn in enumerate(ordenados, start=primer_numero):
        simulacion: dict = {"fraude": txn.fraude}
        if txn.fraude:
            simulacion["patron"] = txn.patron
            simulacion["episodio"] = txn.episodio
        documentos.append(
            {
                "_id": id_transaccion(numero),
                "cuenta_origen": txn.cuenta_origen,
                "sinpe_origen": sinpe_de[txn.cuenta_origen],
                "cuenta_destino": txn.cuenta_destino,
                "monto_crc": txn.monto_crc,
                "moneda": "CRC",
                "canal": "sinpe_movil",
                "fecha": txn.fecha,
                "simulacion": simulacion,
                "esquema_version": ESQUEMA_VERSION,
            }
        )
    return documentos


__all__ = [
    "TxnBorrador",
    "elegir_hora",
    "generar_legitimas",
    "instante",
    "limite_de_la_historia",
    "ordenar_y_numerar",
]
