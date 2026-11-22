"""H-10 · Medicion de efectividad del motor.

Compara las alertas que produjo el motor contra la etiqueta oculta `simulacion` que
escribe el generador (H-05) en cada transaccion: `{fraude, patron, episodio}`.

El motor NO lee `simulacion` (ni D1 ni el evaluador la tocan). La lee solo este
reporte, despues de los hechos, para contestar cuanto fraude se atrapo.

La unidad es la transaccion etiquetada:

- **detectado** (verdadero positivo): fraude con alerta.
- **perdido** (falso negativo): fraude sin alerta.
- **falso positivo**: transaccion legitima con alerta.
- **verdadero negativo**: legitima sin alerta.

Una alerta cuenta sin importar su estado (`nueva`, `descartada`...): lo que se mide es
si el motor la levanto, no lo que decidio despues el agente. Las alertas de
transacciones sin etiqueta (el demo, la Practica 1) no entran en ninguna casilla y se
informan aparte como `alertas_sin_etiqueta`.

Precision = detectados / (detectados + falsos positivos). Exhaustividad = detectados /
(detectados + perdidos). Con denominador cero la metrica es `None` ("no hay con que
medir"), no 0 ni 1: un 0 diria que el motor fallo y un 1 que acerto, y ninguna de las
dos cosas paso. Por patron solo hay exhaustividad: un falso positivo es legitimo, no
tiene patron al que restarle precision.

Ademas, por patron, cuantos EPISODIOS tuvieron al menos una transaccion alertada: en
una cadena de mulas, alertar el primer eslabon ya pone al agente sobre la pista.

El calculo (`calcular_efectividad`, `formatear_tabla`) es puro y se prueba sin base. La
lectura vive en `app/repos/efectividad.py`. Linea de comandos, para correr despues de
cada ajuste de reglas:

    uv run python -m app.deteccion.efectividad
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

SIN_PATRON = "sin_patron"
DECIMALES = 4


@dataclass(frozen=True, slots=True)
class FilaEtiquetada:
    """Un grupo de transacciones con la misma etiqueta, y cuantas tienen alerta.

    El repositorio agrupa por `(fraude, patron, episodio)`: lo legitimo cae en una sola
    fila y el fraude en una por episodio.
    """

    fraude: bool
    patron: str | None
    episodio: str | None
    transacciones: int
    alertadas: int

    def __post_init__(self) -> None:
        if self.transacciones < 0 or self.alertadas < 0:
            raise ValueError("los conteos no pueden ser negativos")
        if self.alertadas > self.transacciones:
            raise ValueError(
                f"{self.alertadas} alertadas de {self.transacciones} transacciones: "
                "una transaccion produce una sola alerta"
            )


class MetricasPatron(BaseModel):
    model_config = ConfigDict(frozen=True)

    patron: str
    fraudes: int
    detectados: int
    perdidos: int
    exhaustividad: float | None
    episodios: int
    episodios_detectados: int


class ReporteEfectividad(BaseModel):
    """Resultado del reporte. Es tambien el cuerpo de `GET /api/v1/efectividad`."""

    model_config = ConfigDict(frozen=True)

    transacciones_etiquetadas: int
    fraudes: int
    legitimas: int
    detectados: int
    perdidos: int
    falsos_positivos: int
    verdaderos_negativos: int
    alertas_totales: int
    alertas_sin_etiqueta: int
    precision: float | None
    exhaustividad: float | None
    por_patron: list[MetricasPatron]


def razon(numerador: int, denominador: int) -> float | None:
    """`numerador / denominador`, o `None` si no hay denominador."""
    if denominador == 0:
        return None
    return round(numerador / denominador, DECIMALES)


def calcular_efectividad(
    filas: Iterable[FilaEtiquetada], *, alertas_totales: int
) -> ReporteEfectividad:
    """El reporte a partir de los grupos etiquetados y del total de alertas."""
    filas = list(filas)
    fraude = [fila for fila in filas if fila.fraude]
    legitimas = [fila for fila in filas if not fila.fraude]

    detectados = sum(fila.alertadas for fila in fraude)
    fraudes = sum(fila.transacciones for fila in fraude)
    falsos_positivos = sum(fila.alertadas for fila in legitimas)
    total_legitimas = sum(fila.transacciones for fila in legitimas)

    alertas_etiquetadas = detectados + falsos_positivos
    if alertas_totales < alertas_etiquetadas:
        raise ValueError(
            f"{alertas_totales} alertas en total pero {alertas_etiquetadas} sobre "
            "transacciones etiquetadas: los conteos se leyeron de momentos distintos"
        )

    por_patron: dict[str, list[FilaEtiquetada]] = {}
    for fila in fraude:
        por_patron.setdefault(fila.patron or SIN_PATRON, []).append(fila)

    metricas = []
    for patron in sorted(por_patron):
        grupo = por_patron[patron]
        del_patron = sum(fila.transacciones for fila in grupo)
        atrapados = sum(fila.alertadas for fila in grupo)
        episodios = {fila.episodio for fila in grupo}
        con_alerta = {fila.episodio for fila in grupo if fila.alertadas > 0}
        metricas.append(
            MetricasPatron(
                patron=patron,
                fraudes=del_patron,
                detectados=atrapados,
                perdidos=del_patron - atrapados,
                exhaustividad=razon(atrapados, del_patron),
                episodios=len(episodios),
                episodios_detectados=len(con_alerta),
            )
        )

    return ReporteEfectividad(
        transacciones_etiquetadas=fraudes + total_legitimas,
        fraudes=fraudes,
        legitimas=total_legitimas,
        detectados=detectados,
        perdidos=fraudes - detectados,
        falsos_positivos=falsos_positivos,
        verdaderos_negativos=total_legitimas - falsos_positivos,
        alertas_totales=alertas_totales,
        alertas_sin_etiqueta=alertas_totales - alertas_etiquetadas,
        precision=razon(detectados, alertas_etiquetadas),
        exhaustividad=razon(detectados, fraudes),
        por_patron=metricas,
    )


def _porcentaje(valor: float | None) -> str:
    return "n/d" if valor is None else f"{100 * valor:.1f} %"


def formatear_tabla(reporte: ReporteEfectividad) -> str:
    """Tabla de texto para la terminal."""
    encabezado = (
        f"{'patron':<30}{'fraudes':>9}{'detect.':>9}{'perdidos':>10}"
        f"{'exhaust.':>10}{'episodios':>11}"
    )
    lineas = [
        "Efectividad del motor (contra la etiqueta oculta `simulacion`)",
        "",
        (
            f"transacciones etiquetadas: {reporte.transacciones_etiquetadas}  "
            f"(fraude {reporte.fraudes}, legitimas {reporte.legitimas})"
        ),
        f"alertas: {reporte.alertas_totales}  (sin etiqueta: {reporte.alertas_sin_etiqueta})",
        (
            f"detectados: {reporte.detectados}  perdidos: {reporte.perdidos}  "
            f"falsos positivos: {reporte.falsos_positivos}  "
            f"verdaderos negativos: {reporte.verdaderos_negativos}"
        ),
        (
            f"precision: {_porcentaje(reporte.precision)}  "
            f"exhaustividad: {_porcentaje(reporte.exhaustividad)}"
        ),
        "",
        encabezado,
        "-" * len(encabezado),
    ]
    for fila in reporte.por_patron:
        lineas.append(
            f"{fila.patron:<30}{fila.fraudes:>9}{fila.detectados:>9}{fila.perdidos:>10}"
            f"{_porcentaje(fila.exhaustividad):>10}"
            f"{f'{fila.episodios_detectados}/{fila.episodios}':>11}"
        )
    lineas.append("-" * len(encabezado))
    lineas.append(
        f"{'total':<30}{reporte.fraudes:>9}{reporte.detectados:>9}{reporte.perdidos:>10}"
        f"{_porcentaje(reporte.exhaustividad):>10}"
    )
    return "\n".join(lineas)


async def _principal() -> None:
    # Importaciones de E/S solo aqui: el modulo sigue siendo puro al importarlo, y
    # `app/repos/efectividad.py` (que importa `FilaEtiquetada`) no forma un ciclo.
    from app.nucleo.config import obtener_ajustes
    from app.nucleo.db import ciclo_cliente, obtener_base
    from app.repos.efectividad import RepoEfectividad

    ajustes = obtener_ajustes()
    async with ciclo_cliente(ajustes) as cliente:
        repo = RepoEfectividad(obtener_base(cliente, ajustes))
        filas, alertas_totales = await repo.leer_filas_etiquetadas()
    print(formatear_tabla(calcular_efectividad(filas, alertas_totales=alertas_totales)))


if __name__ == "__main__":
    asyncio.run(_principal())
