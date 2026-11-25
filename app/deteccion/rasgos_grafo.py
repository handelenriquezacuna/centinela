"""T-63 · Rasgos de grafo por cuenta SINPE: la definicion pura y el procedimiento.

Que es un rasgo. Para una cuenta destino X y una ventana de W minutos, sobre las
transferencias del rango `[desde, corte)`:

- `fan_in`: cuantos origenes distintos le enviaron.
- `fan_out`: a cuantos destinos distintos envio (sale de X, a cualquier hora del rango).
- `monto_recibido_crc`: suma de lo que recibio.
- `monto_reenviado_crc`: suma de lo que X envio dentro de W minutos DESPUES de haber
  recibido (una salida cuenta si existe una entrada en `[salida - W, salida]`), topada en
  lo recibido: no se puede reenviar mas de lo que entro.
- `proporcion_pb`: reenviado sobre recibido en puntos basicos enteros (10.000 = 100 %).
  El dinero nunca pasa por un flotante; el evaluador compara la fraccion exacta.
- `minutos_reenvio`: el mayor tiempo, en minutos redondeados hacia arriba, entre una
  salida contada y la entrada mas reciente que la precede.
- `saltos`: largo de la cadena de reenvios que parte de X (X -> A es 1, X -> A -> B es 2).
  Es el recorrido de `$graphLookup`: cada cuenta se visita una vez y se corta en
  `SALTOS_MAXIMOS`.

Este modulo es la DEFINICION, escrita sin base de datos: `calcular_rasgo` es lo que el
pipeline de `app/repos/rasgos_grafo.py` calcula en el servidor, y una prueba de
integracion comprueba que ambos dan lo mismo. Tambien es lo que usa la calibracion
(`scripts/calibracion/calibrar.py`) para reproducir los rasgos sin Mongo.

El procedimiento, corrible a mano (rubro de procedimientos):

    uv run python -m app.deteccion.rasgos_grafo
    uv run python -m app.deteccion.rasgos_grafo --horas 48

o `python tareas.py rasgos`. Es reejecutable: `$merge` reemplaza el documento de cada
cuenta. Por omision toma la ventana de la regla `red_mulas` del catalogo, este activa o no.

Este modulo no consulta Mongo y no importa `app/api/`.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Protocol

from app.deteccion.evaluador import RasgoRed
from app.modelos import Transaccion

# Disparador que materializa los rasgos: D3 (la extension de H-22). Es el `_id` del
# documento de corrida y el prefijo de los documentos por cuenta.
NOMBRE_RASGOS = "d3_rasgos_grafo"
CLASE_RASGO = "rasgo_grafo"
CLASE_CORRIDA = "rasgo_grafo_corrida"

# Tope del recorrido de la cadena. Con 6 niveles una cadena de mulas real (3 a 4
# eslabones en el generador) cabe de sobra, y un grafo patologico no recorre la base.
SALTOS_MAXIMOS = 6

TIPO_REGLA_RED = "red_mulas"

MS_POR_MINUTO = 60_000


def id_rasgo(cuenta: str) -> str:
    """`_id` del documento de una cuenta: `d3_rasgos_grafo:6033-9001`."""
    return f"{NOMBRE_RASGOS}:{cuenta}"


class ProtocoloRepoRasgos(Protocol):
    """Lo que D3 y el procedimiento necesitan del repositorio."""

    async def recalcular_si_activa(self, corte: datetime) -> int: ...

    async def recalcular(
        self, corte: datetime, *, ventana_minutos: int, horizonte: timedelta
    ) -> int: ...


Indice = Mapping[str, Sequence[Transaccion]]


def indexar(
    transacciones: Iterable[Transaccion], desde: datetime, corte: datetime
) -> tuple[Indice, Indice]:
    """Las transferencias de `[desde, corte)` por destino y por SINPE de origen."""
    por_destino: dict[str, list[Transaccion]] = defaultdict(list)
    por_origen: dict[str, list[Transaccion]] = defaultdict(list)
    for txn in transacciones:
        if not desde <= txn.fecha < corte:
            continue
        por_destino[txn.cuenta_destino].append(txn)
        if txn.sinpe_origen is not None:
            por_origen[txn.sinpe_origen].append(txn)
    return por_destino, por_origen


def _saltos(cuenta: str, por_origen: Indice) -> int:
    """Niveles del recorrido que parte de `cuenta`, como `$graphLookup` con `maxDepth`."""
    visitadas = {cuenta}
    frontera = [cuenta]
    niveles = 0
    while frontera and niveles < SALTOS_MAXIMOS:
        siguiente: list[str] = []
        encontro = False
        for origen in frontera:
            for txn in por_origen.get(origen, ()):
                encontro = True
                if txn.cuenta_destino not in visitadas:
                    visitadas.add(txn.cuenta_destino)
                    siguiente.append(txn.cuenta_destino)
        if not encontro:
            break
        niveles += 1
        frontera = siguiente
    return niveles


def calcular_rasgo(
    cuenta: str,
    por_destino: Indice,
    por_origen: Indice,
    *,
    desde: datetime,
    corte: datetime,
    ventana_minutos: int,
    vigencia_segundos: int,
) -> RasgoRed:
    """El rasgo de `cuenta`; en ceros si no recibio nada en `[desde, corte)`.

    `por_destino` y `por_origen` salen de `indexar` con el mismo `[desde, corte)`.
    """
    recibos = por_destino.get(cuenta, ())
    cero = {
        "cuenta": cuenta,
        "ventana_minutos": ventana_minutos,
        "desde": desde,
        "corte": corte,
        "vigencia_segundos": vigencia_segundos,
    }
    if not recibos:
        return RasgoRed(
            **cero,
            fan_in=0,
            fan_out=0,
            monto_recibido_crc=0,
            monto_reenviado_crc=0,
            proporcion_pb=0,
            minutos_reenvio=0,
            saltos=0,
        )
    salidas = por_origen.get(cuenta, ())
    ventana = timedelta(minutes=ventana_minutos)
    recibido = sum(r.monto_crc for r in recibos)

    contadas = [
        s for s in salidas if any(r.fecha <= s.fecha <= r.fecha + ventana for r in recibos)
    ]
    reenviado = min(sum(s.monto_crc for s in contadas), recibido)

    minutos = 0
    for salida in contadas:
        ultima = max(r.fecha for r in recibos if r.fecha <= salida.fecha)
        milisegundos = (salida.fecha - ultima) // timedelta(milliseconds=1)
        minutos = max(minutos, -(-milisegundos // MS_POR_MINUTO))

    return RasgoRed(
        **cero,
        fan_in=len({r.cuenta_origen for r in recibos}),
        fan_out=len({s.cuenta_destino for s in salidas}),
        monto_recibido_crc=recibido,
        monto_reenviado_crc=reenviado,
        proporcion_pb=reenviado * 10_000 // recibido,
        minutos_reenvio=minutos,
        saltos=_saltos(cuenta, por_origen),
    )


# ---------------------------------------------------------------------------
# Procedimiento: python -m app.deteccion.rasgos_grafo
# ---------------------------------------------------------------------------


def leer_argumentos(argumentos: list[str] | None = None) -> argparse.Namespace:
    analizador = argparse.ArgumentParser(
        prog="python -m app.deteccion.rasgos_grafo",
        description="Recalcula los rasgos de grafo por cuenta (T-63, R-11).",
    )
    analizador.add_argument(
        "--ventana-minutos",
        type=int,
        help="ventana del reenvio; por omision, la de la regla red_mulas del catalogo",
    )
    analizador.add_argument(
        "--horas",
        type=float,
        help="horizonte hacia atras desde ahora; por omision, rasgos_grafo.horizonte_horas",
    )
    args = analizador.parse_args(argumentos)
    if args.ventana_minutos is not None and args.ventana_minutos < 1:
        analizador.error("--ventana-minutos tiene que ser al menos 1")
    if args.horas is not None and args.horas <= 0:
        analizador.error("--horas tiene que ser mayor que 0")
    return args


async def _principal(argumentos: list[str] | None = None) -> None:
    from datetime import UTC

    from app.nucleo.config import obtener_ajustes
    from app.nucleo.db import ciclo_cliente, obtener_base
    from app.repos.rasgos_grafo import RepoRasgosGrafo

    args = leer_argumentos(argumentos)
    ajustes = obtener_ajustes()
    horizonte = timedelta(hours=args.horas or ajustes.rasgos_grafo.horizonte_horas)
    async with ciclo_cliente(ajustes) as cliente:
        repo = RepoRasgosGrafo(obtener_base(cliente, ajustes), ajustes.rasgos_grafo)
        ventana = args.ventana_minutos or await repo.ventana_de_la_regla()
        if ventana is None:
            raise SystemExit("no hay regla red_mulas con ventana_minutos; pasar --ventana-minutos")
        corte = datetime.now(UTC)
        cuentas = await repo.recalcular(corte, ventana_minutos=ventana, horizonte=horizonte)
        mejores = await repo.mayores_proporciones(5)
    print(
        f"rasgos de grafo: {cuentas} cuenta(s) recalculadas, ventana {ventana} minutos, "
        f"horizonte {horizonte}"
    )
    for fila in mejores:
        print(
            f"  {fila['cuenta']}  recibido={fila['monto_recibido_crc']:>9}  "
            f"reenviado={fila['monto_reenviado_crc']:>9}  "
            f"proporcion={fila['proporcion_pb'] / 100:5.1f} %  saltos={fila['saltos']}"
        )


if __name__ == "__main__":
    asyncio.run(_principal())
