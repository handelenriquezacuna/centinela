"""D-68 · Demo en vivo de R-11: la cadena de mulas con fecha de AHORA.

    uv run python -m app.deteccion.demo_cadena               # inserta la cadena y calcula el rasgo
    uv run python -m app.deteccion.demo_cadena --disparar    # ademas, una victima nueva hacia 6033-9001

o `python tareas.py demo-cadena-viva [--disparar]` (respeta CENTINELA_MONGO__URI).

Los datos del demo son de setiembre y el rasgo de R-11 solo mira las ultimas horas, asi que
para ensenar R-11 en vivo hay que poner la cadena con la fecha de ahora. Esto inserta, hacia
atras desde este instante, la cadena del caso (UC-04): una victima envia 280.000 a 6033-9001
y, 4 minutos despues, 276.000 de 6033-9001 a 7104-2288, 185.000 a 8455-1177 (+9) y 90.000 a
6001-3344 (+11). Despues recalcula los rasgos con la misma funcion que D3 e imprime el de
6033-9001 (98,6 %).

Con `--disparar` agrega una transferencia nueva hacia 6033-9001, fechada ahora: si D1 esta
corriendo y R-11 activa, levanta una alerta con R-11 y la explicacion en el panel.

No borra ni modifica nada y se puede correr varias veces (ids nuevos cada vez). Ojo: el
rasgo mira las ultimas 24 horas, y la transferencia de `--disparar` es dinero que 6033-9001
recibio y no reenvio; una segunda corrida dentro del horizonte baja la proporcion (con
--disparar ya no llega a 80 % y R-11 deja de disparar). Una corrida por ensayo. Necesita el
demo cargado (`python tareas.py datos-demo`): las cuentas CTA-001, CTA-002 y CTA-005..007.

Este modulo no consulta Mongo directo (usa `app/repos/`) y no importa `app/api/`.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.deteccion.evaluador import RasgoRed
from app.modelos import Transaccion

VICTIMA = "8712-4455"  # CTA-001, Maria Rodriguez: la del caso
OTRA_VICTIMA = "6210-3387"  # CTA-002: la que transfiere despues, la que dispara R-11
MULA = "6033-9001"
# (SINPE de origen, SINPE de destino, monto, minutos desde la primera)
ESLABONES = (
    (VICTIMA, MULA, 280_000, 0),
    (MULA, "7104-2288", 276_000, 4),
    ("7104-2288", "8455-1177", 185_000, 9),
    ("8455-1177", "6001-3344", 90_000, 11),
)
MONTO_DISPARO = 250_000
ATRAS = timedelta(minutes=12)  # el ultimo eslabon cae un minuto antes de ahora


@dataclass(frozen=True)
class Cadena:
    transacciones: tuple[Transaccion, ...]
    disparo: Transaccion


def sinpes_necesarios() -> tuple[str, ...]:
    return tuple(dict.fromkeys([OTRA_VICTIMA, *(o for o, *_ in ESLABONES)]))


def armar_cadena(
    ahora: datetime, primer_numero: int, cuentas: dict[str, str]
) -> Cadena:
    """La cadena y la transferencia de disparo, con ids consecutivos. Funcion pura.

    `cuentas` es `cuenta_sinpe -> CTA-...`. Falta alguna: se dice cual y que falta cargar.
    """
    faltan = [s for s in sinpes_necesarios() if s not in cuentas]
    if faltan:
        raise LookupError(
            f"faltan las cuentas {faltan} en `cuentas`; cargar el demo con "
            "`python tareas.py datos-demo`"
        )
    inicio = ahora - ATRAS

    def txn(n: int, origen: str, destino: str, monto: int, fecha: datetime) -> Transaccion:
        return Transaccion(
            _id=f"TXN-{n:06d}",
            cuenta_origen=cuentas[origen],
            sinpe_origen=origen,
            cuenta_destino=destino,
            monto_crc=monto,
            fecha=fecha,
        )

    cadena = tuple(
        txn(primer_numero + i, o, d, monto, inicio + timedelta(minutes=minutos))
        for i, (o, d, monto, minutos) in enumerate(ESLABONES)
    )
    disparo = txn(primer_numero + len(ESLABONES), OTRA_VICTIMA, MULA, MONTO_DISPARO, ahora)
    return Cadena(cadena, disparo)


def describir(rasgo: RasgoRed) -> str:
    return (
        f"rasgo de {rasgo.cuenta}: recibido {rasgo.monto_recibido_crc} CRC, reenviado "
        f"{rasgo.monto_reenviado_crc} CRC, proporcion {f"{rasgo.proporcion_pb / 100:.1f}".replace(".", ",")} %, "
        f"{rasgo.minutos_reenvio} minutos, {rasgo.saltos} saltos"
    )


async def ejecutar(base, *, disparar: bool, ahora: datetime | None = None) -> dict:
    """Inserta la cadena, recalcula los rasgos y devuelve lo que paso (para imprimir y probar)."""
    from app.nucleo.config import obtener_ajustes
    from app.repos.demo_cadena import RepoDemoCadena
    from app.repos.rasgos_grafo import RepoRasgosGrafo

    ajustes = obtener_ajustes()
    repo = RepoDemoCadena(base)
    rasgos = RepoRasgosGrafo(base, ajustes.rasgos_grafo)

    ventana = await rasgos.ventana_de_la_regla()
    if ventana is None:
        raise LookupError("no hay regla red_mulas con ventana_minutos; sembrar 03-catalogos.js")
    activa = await rasgos.ventana_de_la_regla(solo_activa=True) is not None

    ahora = ahora or datetime.now(UTC)
    cuentas = await repo.cuentas_por_sinpe(sinpes_necesarios())
    cadena = armar_cadena(ahora, await repo.siguiente_numero(), cuentas)
    await repo.insertar(cadena.transacciones)

    await rasgos.recalcular(
        ahora,
        ventana_minutos=ventana,
        horizonte=timedelta(hours=ajustes.rasgos_grafo.horizonte_horas),
    )
    rasgo = await rasgos.leer_rasgo(MULA)

    disparo = None
    if disparar:
        # Despues del recalculo, para que el rasgo exista cuando D1 evalua esta transferencia.
        disparo = cadena.disparo.model_copy(update={"fecha": datetime.now(UTC)})
        await repo.insertar([disparo])
    return {
        "cadena": cadena.transacciones,
        "rasgo": rasgo,
        "disparo": disparo,
        "regla_activa": activa,
    }


def leer_argumentos(argumentos: list[str] | None = None) -> argparse.Namespace:
    analizador = argparse.ArgumentParser(
        prog="python -m app.deteccion.demo_cadena",
        description="Cadena de mulas con fecha de ahora, para ensenar R-11 en vivo (D-68).",
    )
    analizador.add_argument(
        "--disparar",
        action="store_true",
        help="ademas inserta una transferencia nueva hacia 6033-9001 para que D1 levante la alerta",
    )
    return analizador.parse_args(argumentos)


async def _principal(argumentos: list[str] | None = None) -> None:
    from app.nucleo.config import obtener_ajustes
    from app.nucleo.db import ciclo_cliente, obtener_base

    args = leer_argumentos(argumentos)
    ajustes = obtener_ajustes()
    async with ciclo_cliente(ajustes) as cliente:
        resultado = await ejecutar(obtener_base(cliente, ajustes), disparar=args.disparar)
    cadena = resultado["cadena"]
    print(f"cadena insertada: {cadena[0].transaccion_id} a {cadena[-1].transaccion_id}")
    for txn in cadena:
        print(
            f"  {txn.transaccion_id}  {txn.sinpe_origen} -> {txn.cuenta_destino}  "
            f"{txn.monto_crc:>8} CRC  {txn.fecha:%H:%M:%S} UTC"
        )
    print(describir(resultado["rasgo"]))
    if not resultado["regla_activa"]:
        print("aviso: la regla R-11 esta inactiva; D3 no mantiene los rasgos ni D1 la evalua")
    if resultado["disparo"] is not None:
        d = resultado["disparo"]
        print(
            f"transferencia de disparo: {d.transaccion_id} ({d.sinpe_origen} -> {d.cuenta_destino}, "
            f"{d.monto_crc} CRC). Con D1 corriendo, buscar la alerta de {d.transaccion_id} en el "
            "panel: trae R-11. El rasgo vale 60 s (D3 lo renueva si esta corriendo)."
        )


if __name__ == "__main__":
    asyncio.run(_principal())
