"""Geometria de los graficos del tablero (H-20) y del grafo de la red de mulas (T-35).

El servidor calcula y la interfaz solo dibuja: lo usan las plantillas HTMX y la API JSON
(`GET /api/v1/tablero`, `GET /api/v1/red-mulas/{cuenta}/grafo`) que lee la SPA (T-61). Son
funciones puras, en pixeles enteros; ni una consulta, ni una libreria de grafos en el
navegador.
"""

from __future__ import annotations

from typing import Any

from app.repos.red_mulas import RedMulas


def tiempo_corto(segundos: float | None) -> str:
    """90 -> '1 min 30 s'; 3700 -> '1 h 1 min'. Para las cifras del tablero."""
    if segundos is None:
        return "-"
    total = round(segundos)
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas} h {minutos} min"
    if minutos:
        return f"{minutos} min {seg} s"
    return f"{seg} s"


ETIQUETA, ANCHO_MAX, ALTO_FILA = 120, 260, 26


def barras_horizontales(filas: list[tuple[str, int, str]]) -> dict[str, Any]:
    """`filas` = (rotulo, valor, clase). Devuelve la geometria de un grafico de barras."""
    maximo = max((valor for _, valor, _ in filas), default=0)
    return {
        "ancho": ETIQUETA + ANCHO_MAX + 50,
        "alto": max(1, len(filas)) * ALTO_FILA,
        "etiqueta": ETIQUETA,
        "filas": [
            {
                "y": i * ALTO_FILA,
                "rotulo": rotulo,
                "valor": valor,
                "clase": clase,
                # Una barra con valor > 0 nunca desaparece por ser chica.
                "ancho": max(2, round(valor * ANCHO_MAX / maximo)) if valor > 0 else 0,
            }
            for i, (rotulo, valor, clase) in enumerate(filas)
        ],
    }


def columnas_por_hora(por_hora: dict[str, int]) -> dict[str, Any]:
    alto_max, paso = 120, 20
    maximo = max(por_hora.values(), default=0)
    columnas = []
    for hora, valor in sorted(por_hora.items()):
        alto = max(2, round(valor * alto_max / maximo)) if valor > 0 else 0
        columnas.append(
            {"x": int(hora) * paso, "y": alto_max - alto, "alto": alto, "hora": hora, "valor": valor}
        )
    return {"ancho": 24 * paso, "alto": alto_max + 20, "base": alto_max, "paso": paso, "columnas": columnas}


def graficos_tablero(resumen: dict[str, Any]) -> dict[str, Any]:
    actual = resumen["actual"]
    graficos: dict[str, Any] = {
        "estado": barras_horizontales(
            [(k, v, f"barra-{k}") for k, v in actual["por_estado"].items()]
        ),
        "severidad": barras_horizontales(
            [(k, v, f"barra-{k.lower()}") for k, v in actual["por_severidad"].items()]
        ),
        "agente": None,
        "hora": None,
    }
    if actual["por_agente"] is not None:
        orden = sorted(actual["por_agente"].items(), key=lambda par: (-par[1], par[0]))
        graficos["agente"] = barras_horizontales([(k, v, "barra-agente") for k, v in orden])
    if actual["por_hora"] is not None:
        graficos["hora"] = columnas_por_hora(actual["por_hora"])
    return graficos



COLUMNA, FILA, NODO_ANCHO, NODO_ALTO, MARGEN = 230, 84, 140, 44, 20


def disponer_grafo(red: RedMulas) -> dict[str, Any]:
    """Posiciones del grafo: una columna por nivel, los nodos de un nivel apilados."""
    por_nivel: dict[int, list[Any]] = {}
    for nodo in red.nodos:
        por_nivel.setdefault(nodo.nivel, []).append(nodo)
    filas_max = max((len(v) for v in por_nivel.values()), default=1)
    posiciones: dict[str, tuple[int, int]] = {}
    nodos = []
    for nivel, grupo in sorted(por_nivel.items()):
        desplazamiento = (filas_max - len(grupo)) * FILA // 2
        for i, nodo in enumerate(grupo):
            x = MARGEN + nivel * COLUMNA
            y = MARGEN + desplazamiento + i * FILA
            posiciones[nodo.cuenta] = (x, y)
            nodos.append(
                {
                    "x": x,
                    "y": y,
                    "ancho": NODO_ANCHO,
                    "alto": NODO_ALTO,
                    "cuenta": nodo.cuenta,
                    "cuenta_id": nodo.cuenta_id,
                    "externa": nodo.externa,
                    "en_lista": nodo.en_lista_riesgo,
                    "inicio": nodo.nivel == 0,
                }
            )
    aristas = []
    for arista in red.aristas:
        ox, oy = posiciones[arista.origen]
        dx, dy = posiciones[arista.destino]
        if dx > ox:  # hacia adelante: del borde derecho al izquierdo
            x1, y1, x2, y2 = ox + NODO_ANCHO, oy + NODO_ALTO // 2, dx, dy + NODO_ALTO // 2
        else:  # un retorno (cuenta que ya aparecio): de centro a centro, por abajo
            x1, y1 = ox + NODO_ANCHO // 2, oy + NODO_ALTO
            x2, y2 = dx + NODO_ANCHO // 2, dy + (NODO_ALTO if dy >= oy else 0)
        aristas.append(
            {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "tx": (x1 + x2) // 2,
                "ty": (y1 + y2) // 2 - 6,
                "monto_crc": arista.monto_crc,
                "minutos": arista.minutos_desde_anterior,
                "salto": arista.salto,
            }
        )
    niveles = max(por_nivel, default=0)
    return {
        "ancho": MARGEN * 2 + niveles * COLUMNA + NODO_ANCHO,
        "alto": MARGEN * 2 + (filas_max - 1) * FILA + NODO_ALTO,
        "nodos": nodos,
        "aristas": aristas,
    }
