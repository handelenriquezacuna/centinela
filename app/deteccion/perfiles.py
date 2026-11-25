"""H-21 / T-76 · Perfiles de comportamiento: la definicion pura y el procedimiento.

Un perfil resume lo que un cliente hace normalmente, para que R-01 (monto atipico) y R-05
(horario inusual) tengan contra que comparar sin recalcularlo en cada transaccion. Se
calcula con el pipeline de `app/repos/perfiles.py` y se escribe con `$merge` en
`perfiles_comportamiento`; este modulo es la DEFINICION de cada numero, escrita sin base
de datos, y lo que una prueba de integracion compara contra el pipeline.

Que transacciones cuentan. Las que el cliente ENVIO (por sus cuentas, `cuenta_origen`)
en `[corte - dias, corte)` y que NO tienen una alerta viva: una alerta en estado
`nueva`, `en_revision`, `confirmada` o `escalada` saca a su transaccion del perfil, y una
`descartada` (falso positivo, la transaccion era legitima) la deja. Es el criterio que
evita que el fraude se vuelva "normal" para el motor, sin que el calculo lea la etiqueta
oculta `simulacion` (esa la lee solo H-10).

Los numeros, todos con aritmetica entera:

- `num_transferencias`: cuantas transacciones cuentan.
- `promedio_crc`: suma / cantidad redondeada a la unidad, mitad hacia arriba. Es
  `(2 * suma + n) // (2 * n)`: ningun flotante toca el dinero.
- `maximo_crc`: el mayor monto.
- `desviacion_crc`: desviacion estandar POBLACIONAL, `sqrt(n * suma_cuadrados - suma^2) / n`,
  redondeada a la unidad con `floor(x + 0.5)`. Una sola transferencia da 0.
- `horario_habitual`: ver `ventana_horaria`.
- `destinos_frecuentes`: las cuentas destino con al menos 2 transferencias, de mas a
  menos veces (empate: por numero), hasta 10. Si ninguna se repite, la mas usada (el
  validador exige al menos un destino).

Un cliente sin transacciones que cuenten en el rango NO recibe perfil nuevo, y el que ya
tenia se conserva tal cual: sin historial no hay con que recalcular, y borrarlo dejaria a
R-01 y R-05 sin evaluar justo cuando mas falta hacen.

El procedimiento, corrible a mano (rubro de procedimientos):

    uv run python -m app.deteccion.perfiles                    # todos, 90 dias hasta ahora
    uv run python -m app.deteccion.perfiles --dias 30 --cliente CLI-001

o `python tareas.py perfiles`. Es reejecutable: `$merge` reemplaza el perfil de cada
cliente. El equivalente en mongosh es `scripts/procedimientos/03-recalcular-perfiles.js`.

Este modulo no consulta Mongo y no importa `app/api/`.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Protocol

DIAS_POR_OMISION = 90
# Costa Rica: UTC-6 fijo (igual que `app/repos/indicadores.py`).
ZONA_CR = timezone(timedelta(hours=-6), "America/Costa_Rica")
# Que parte de las transferencias tiene que caer dentro del horario habitual, en por ciento.
COBERTURA_HORARIO_PCT = 90
# Cuantos destinos frecuentes se guardan como maximo, y desde cuantas veces es "frecuente".
MAX_DESTINOS_FRECUENTES = 10
MIN_VECES_FRECUENTE = 2


class ProtocoloRepoPerfiles(Protocol):
    """Lo que D3 y el procedimiento necesitan del repositorio."""

    async def recalcular(
        self, corte: datetime, *, dias: int = DIAS_POR_OMISION, cliente_id: str | None = None
    ) -> int: ...


def ventana_horaria(histograma: list[int]) -> tuple[int, int]:
    """Horario habitual: la ventana de horas de CR mas corta que reune el 90 % de las
    transferencias.

    `histograma[h]` es cuantas transferencias cayeron en la hora `h` de Costa Rica. La
    ventana es circular (puede cruzar la medianoche: 22 a 06). Se elige la de menos horas
    que contenga al menos `COBERTURA_HORARIO_PCT` % del total; si hay empate, la que
    empieza mas temprano. Devuelve `(hora_inicio, hora_fin)`, ambas incluidas: la ventana
    se escribe `HH:00` a `HH:59`. Con 24 horas, `00:00` a `23:59`.
    """
    total = sum(histograma)
    doble = histograma + histograma
    mejor: tuple[int, int] | None = None
    for inicio in range(24):
        for largo in range(1, 25):
            cubiertas = sum(doble[inicio : inicio + largo])
            if cubiertas * 100 >= total * COBERTURA_HORARIO_PCT:
                if mejor is None or (largo, inicio) < mejor:
                    mejor = (largo, inicio)
                break
    assert mejor is not None  # con 24 horas siempre se cubre el total
    largo, inicio = mejor
    return inicio, (inicio + largo - 1) % 24


def calcular_perfil(
    transferencias: Iterable[tuple[int, str, datetime]],
) -> dict[str, Any] | None:
    """El perfil a partir de `(monto_crc, cuenta_destino, fecha_utc)`, o None si no hay."""
    montos: list[int] = []
    destinos: Counter[str] = Counter()
    histograma = [0] * 24
    for monto, destino, fecha in transferencias:
        montos.append(monto)
        destinos[destino] += 1
        histograma[fecha.astimezone(ZONA_CR).hour] += 1
    if not montos:
        return None

    n = len(montos)
    suma = sum(montos)
    suma_cuadrados = sum(m * m for m in montos)
    hora_inicio, hora_fin = ventana_horaria(histograma)

    ordenados = sorted(destinos.items(), key=lambda par: (-par[1], par[0]))
    frecuentes = [cuenta for cuenta, veces in ordenados if veces >= MIN_VECES_FRECUENTE]
    frecuentes = frecuentes[:MAX_DESTINOS_FRECUENTES] or [ordenados[0][0]]

    return {
        "promedio_crc": (2 * suma + n) // (2 * n),
        "maximo_crc": max(montos),
        "desviacion_crc": math.floor(math.sqrt(n * suma_cuadrados - suma * suma) / n + 0.5),
        "num_transferencias": n,
        "horario_habitual": {"inicio": f"{hora_inicio:02d}:00", "fin": f"{hora_fin:02d}:59"},
        "destinos_frecuentes": frecuentes,
    }


# ---------------------------------------------------------------------------
# Procedimiento: python -m app.deteccion.perfiles
# ---------------------------------------------------------------------------


def leer_argumentos(argumentos: list[str] | None = None) -> argparse.Namespace:
    analizador = argparse.ArgumentParser(
        prog="python -m app.deteccion.perfiles",
        description="Recalcula perfiles_comportamiento (H-21).",
    )
    analizador.add_argument(
        "--dias",
        type=int,
        default=DIAS_POR_OMISION,
        help=f"cuantos dias hacia atras desde el corte (por omision {DIAS_POR_OMISION})",
    )
    analizador.add_argument("--cliente", help="un solo cliente, por ejemplo CLI-001; sin esto, todos")
    analizador.add_argument(
        "--corte",
        type=datetime.fromisoformat,
        help="fin del rango, ISO 8601 en UTC; por omision, ahora",
    )
    args = analizador.parse_args(argumentos)
    if args.dias < 1:
        analizador.error("--dias tiene que ser al menos 1")
    return args


async def _principal(argumentos: list[str] | None = None) -> None:
    from app.nucleo.config import obtener_ajustes
    from app.nucleo.db import ciclo_cliente, obtener_base
    from app.repos.perfiles import RepoPerfiles

    args = leer_argumentos(argumentos)
    corte = args.corte or datetime.now(UTC)
    if corte.tzinfo is None:
        corte = corte.replace(tzinfo=UTC)
    ajustes = obtener_ajustes()
    async with ciclo_cliente(ajustes) as cliente:
        repo = RepoPerfiles(obtener_base(cliente, ajustes))
        perfiles = await repo.recalcular(corte, dias=args.dias, cliente_id=args.cliente)
        muestra = await repo.leer_perfiles(args.cliente, limite=5)
    alcance = args.cliente or "todos los clientes"
    print(f"perfiles_comportamiento: {perfiles} perfil(es) recalculados ({alcance}, {args.dias} dias)")
    for perfil in muestra:
        horario = perfil["horario_habitual"]
        print(
            f"  {perfil['_id']}  n={perfil['num_transferencias']:>4}  "
            f"promedio={perfil['promedio_crc']:>8}  maximo={perfil['maximo_crc']:>8}  "
            f"horario={horario['inicio']}-{horario['fin']}"
        )


if __name__ == "__main__":
    asyncio.run(_principal())
