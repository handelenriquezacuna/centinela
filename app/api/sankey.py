"""Sankey del recorrido del dinero (T-55): la geometria, en Python puro.

Recibe la red de mulas (`RedMulas`, la misma que sirve `GET /api/v1/red-mulas/{cuenta}`) y
devuelve posiciones, anchos y trazados en pixeles. La plantilla `paginas/sankey.html`
solo dibuja lo que este modulo calcula: no hay JavaScript ni ninguna consulta aqui.

Reglas del dibujo:

- Una columna por salto. El nodo es una (cuenta, columna): el origen de una transferencia
  del salto k esta en la columna k-1 y su destino en la columna k. Asi una cuenta que
  reaparece mas adelante (un retorno) es otro nodo en otra columna y nunca una banda hacia
  atras. La marca de lista de riesgo es de la cuenta, no del nodo.
- El grosor de una banda es proporcional a los colones: `round(monto * ALTO_BANDA_MAX /
  monto_mayor)`, entero, con un minimo de `GROSOR_MINIMO` para que una banda chica no
  desaparezca. El dinero nunca pasa por un flotante: el monto sigue siendo el entero de la
  base y el grosor es un derivado de presentacion.
- Con `victima` (el Sankey parte de una transaccion, como en el detalle de la alerta) la
  columna 0 es el cliente victima y la primera banda es su transferencia hacia la cuenta de
  partida de la red; los saltos de la red empiezan en la columna 1. La victima no es una
  cuenta de la red: no lleva marca de lista de riesgo ni boton para marcarla.
- La altura de un nodo es la mayor entre la suma de sus bandas de entrada y de salida.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.api.formatos import colones
from app.repos.detalle import ProtocoloRepoDetalle
from app.repos.red_mulas import RedMulas

COLUMNA_PASO = 230  # distancia horizontal entre columnas
NODO_ANCHO = 16
MARGEN_X = 24
MARGEN_DERECHO = 150  # espacio para la etiqueta del ultimo nodo
MARGEN_SUPERIOR = 46  # espacio para la etiqueta del primer nodo
MARGEN_INFERIOR = 20
SEPARACION = 52  # entre nodos de una columna: cabe su etiqueta de dos lineas
ALTO_BANDA_MAX = 140  # grosor, en px, de la banda de mayor monto
GROSOR_MINIMO = 2
ALTO_NODO_MINIMO = 20


def grosor_px(monto_crc: int, monto_mayor: int) -> int:
    """Grosor entero de una banda: proporcional al monto, con minimo `GROSOR_MINIMO`."""
    if monto_crc <= 0 or monto_mayor <= 0:
        return 0
    return max(GROSOR_MINIMO, round(monto_crc * ALTO_BANDA_MAX / monto_mayor))


@dataclass(frozen=True)
class Victima:
    """El cliente que origino la transferencia de partida (D-54)."""

    nombre: str
    cuenta: str  # su numero SINPE, o el CTA- si la transaccion no lo trae
    transaccion_id: str
    monto_crc: int
    fecha: datetime


@dataclass
class _Nodo:
    cuenta: str
    columna: int
    orden: int
    entrada_px: int = 0
    salida_px: int = 0
    x: int = 0
    y: int = 0
    alto: int = 0
    desplazamiento_entrada: int = 0
    desplazamiento_salida: int = 0
    cuenta_id: str | None = None
    externa: bool = False
    en_lista: bool = False
    victima: bool = False
    nombre: str | None = None


@dataclass
class Sankey:
    """Lo que la plantilla dibuja. Todo en px enteros salvo los montos (colones enteros)."""

    ancho: int
    alto: int
    columnas: int
    saltos: int
    transferencias: int
    resumen: str
    nodos: list[dict[str, Any]] = field(default_factory=list)
    bandas: list[dict[str, Any]] = field(default_factory=list)
    cuentas: list[dict[str, Any]] = field(default_factory=list)


def _resumen(red: RedMulas, saltos: int, victima: Victima | None, montos: list[int]) -> str:
    if victima is not None:
        return (
            f"Recorrido del dinero desde la víctima {victima.nombre}: {len(montos)} "
            f"transferencias, de {colones(montos[0])} a {colones(montos[-1])} en la última"
        )
    return (
        f"Recorrido del dinero desde la cuenta {red.cuenta}: {saltos} "
        f"{'salto' if saltos == 1 else 'saltos'}, de {colones(montos[0])} "
        f"a {colones(montos[-1])} en el último"
    )


def disponer_sankey(red: RedMulas, victima: Victima | None = None) -> Sankey | None:
    """La disposicion del Sankey de `red`, o `None` si no hay ninguna transferencia."""
    if not red.aristas:
        return None
    aristas = sorted(red.aristas, key=lambda a: (a.salto, a.fecha, a.transaccion_id))
    desplazo = 1 if victima is not None else 0
    monto_mayor = max(
        [a.monto_crc for a in aristas] + ([victima.monto_crc] if victima is not None else [])
    )
    por_cuenta = {nodo.cuenta: nodo for nodo in red.nodos}

    nodos: dict[tuple[str, int], _Nodo] = {}

    def nodo(cuenta: str, columna: int) -> _Nodo:
        clave = (cuenta, columna)
        if clave not in nodos:
            informacion = por_cuenta.get(cuenta)
            nodos[clave] = _Nodo(
                cuenta=cuenta,
                columna=columna,
                orden=len(nodos),
                cuenta_id=informacion.cuenta_id if informacion else None,
                externa=informacion.externa if informacion else True,
                en_lista=informacion.en_lista_riesgo if informacion else False,
            )
        return nodos[clave]

    # (origen, destino, monto, fecha, transaccion_id, salto) de cada banda, en orden.
    tramos: list[tuple[_Nodo, _Nodo, int, datetime, str, int]] = []
    if victima is not None:
        origen_victima = _Nodo(
            cuenta=victima.cuenta,
            columna=0,
            orden=-1,
            externa=False,
            victima=True,
            nombre=victima.nombre,
        )
        nodos[("victima", 0)] = origen_victima
        tramos.append(
            (
                origen_victima,
                nodo(red.cuenta, 1),
                victima.monto_crc,
                victima.fecha,
                victima.transaccion_id,
                0,
            )
        )
    for arista in aristas:
        tramos.append(
            (
                nodo(arista.origen, arista.salto - 1 + desplazo),
                nodo(arista.destino, arista.salto + desplazo),
                arista.monto_crc,
                arista.fecha,
                arista.transaccion_id,
                arista.salto,
            )
        )
    enlaces: list[tuple[tuple[Any, ...], _Nodo, _Nodo, int]] = []
    for tramo in tramos:
        origen, destino, monto = tramo[0], tramo[1], tramo[2]
        grosor = grosor_px(monto, monto_mayor)
        origen.salida_px += grosor
        destino.entrada_px += grosor
        enlaces.append((tramo, origen, destino, grosor))

    columnas = max(clave[1] for clave in nodos) + 1
    por_columna: dict[int, list[_Nodo]] = {}
    for n in sorted(nodos.values(), key=lambda n: n.orden):
        n.alto = max(n.entrada_px, n.salida_px, ALTO_NODO_MINIMO)
        por_columna.setdefault(n.columna, []).append(n)

    def alto_de(columna: list[_Nodo]) -> int:
        return sum(n.alto for n in columna) + SEPARACION * (len(columna) - 1)

    mayor = max(alto_de(c) for c in por_columna.values())
    for indice, columna in por_columna.items():
        y = MARGEN_SUPERIOR + (mayor - alto_de(columna)) // 2
        for n in columna:
            n.x = MARGEN_X + indice * COLUMNA_PASO
            n.y = y
            y += n.alto + SEPARACION

    bandas = []
    for tramo, origen, destino, grosor in enlaces:
        _, _, monto, fecha, transaccion_id, salto = tramo
        x0, x1 = origen.x + NODO_ANCHO, destino.x
        y0 = origen.y + origen.desplazamiento_salida
        y1 = destino.y + destino.desplazamiento_entrada
        origen.desplazamiento_salida += grosor
        destino.desplazamiento_entrada += grosor
        medio = (x0 + x1) // 2
        bandas.append(
            {
                "d": (
                    f"M{x0} {y0} C{medio} {y0} {medio} {y1} {x1} {y1} "
                    f"L{x1} {y1 + grosor} C{medio} {y1 + grosor} {medio} {y0 + grosor} "
                    f"{x0} {y0 + grosor} Z"
                ),
                "grosor": grosor,
                "tx": medio,
                "ty": (y0 + y1 + grosor) // 2 + 4,
                "salto": salto,
                "origen": origen.cuenta,
                "destino": destino.cuenta,
                "monto_crc": monto,
                "fecha": fecha,
                "transaccion_id": transaccion_id,
            }
        )

    vistas: dict[str, dict[str, Any]] = {}
    for n in sorted(nodos.values(), key=lambda n: (n.columna, n.orden)):
        vistas.setdefault(
            f"victima:{n.cuenta}" if n.victima else n.cuenta,
            {
                "victima": n.victima,
                "nombre": n.nombre,
                "cuenta": n.cuenta,
                "cuenta_id": n.cuenta_id,
                "externa": n.externa,
                "en_lista": n.en_lista,
                "columna": n.columna,
            },
        )
    saltos = max(a.salto for a in aristas)
    return Sankey(
        ancho=MARGEN_X + (columnas - 1) * COLUMNA_PASO + NODO_ANCHO + MARGEN_DERECHO,
        alto=MARGEN_SUPERIOR + mayor + MARGEN_INFERIOR,
        columnas=columnas,
        saltos=saltos,
        transferencias=len(tramos),
        resumen=_resumen(red, saltos, victima, [t[2] for t in tramos]),
        nodos=[
            {
                "x": n.x,
                "y": n.y,
                "ancho": NODO_ANCHO,
                "alto": n.alto,
                "cuenta": n.cuenta,
                "columna": n.columna,
                "externa": n.externa,
                "en_lista": n.en_lista,
                "inicio": n.columna == 0 and not n.victima,
                "victima": n.victima,
                "nombre": n.nombre,
            }
            for n in sorted(nodos.values(), key=lambda n: (n.columna, n.orden))
        ],
        bandas=bandas,
        cuentas=list(vistas.values()),
    )


ID_TRANSACCION = re.compile(r"^TXN-\d{3,}$")


async def victima_de(
    repo: ProtocoloRepoDetalle, transaccion_id: str | None
) -> Victima | None:
    """La victima de la transaccion de partida, si hay una y existe (D-54)."""
    if not transaccion_id or not ID_TRANSACCION.match(transaccion_id):
        return None
    contexto = await repo.contexto(transaccion_id)
    transaccion, cliente = contexto["transaccion"], contexto["cliente"]
    if transaccion is None or cliente is None:
        return None
    return Victima(
        nombre=cliente["nombre"],
        cuenta=transaccion.get("sinpe_origen") or transaccion["cuenta_origen"],
        transaccion_id=transaccion["_id"],
        monto_crc=int(transaccion["monto_crc"]),
        fecha=transaccion["fecha"],
    )
