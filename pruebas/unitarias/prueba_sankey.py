"""T-55 · La geometria del Sankey, sin base y sin plantilla: bandas, columnas y repetidas."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

from app.api.sankey import (
    ALTO_BANDA_MAX,
    COLUMNA_PASO,
    GROSOR_MINIMO,
    NODO_ANCHO,
    Victima,
    disponer_sankey,
    grosor_px,
)
from app.repos.red_mulas import AristaRed, NodoRed, RedMulas

T0 = datetime(2026, 11, 19, 3, 47, tzinfo=UTC)


def _red(tramos: list[tuple[str, str, int]], listadas: frozenset[str] = frozenset()) -> RedMulas:
    """`tramos` = (origen, destino, monto) en orden de salto, uno por salto."""
    aristas = [
        AristaRed(
            transaccion_id=f"TXN-{i + 1:03d}",
            origen=origen,
            destino=destino,
            monto_crc=monto,
            fecha=T0 + timedelta(minutes=5 * i),
            salto=i + 1,
            minutos_desde_anterior=None if i == 0 else 5.0,
        )
        for i, (origen, destino, monto) in enumerate(tramos)
    ]
    niveles: dict[str, int] = {tramos[0][0]: 0}
    for arista in aristas:
        niveles.setdefault(arista.destino, arista.salto)
    return RedMulas(
        cuenta=tramos[0][0],
        desde=T0,
        ventana_minutos=60,
        saltos_maximos=6,
        saltos_recorridos=len(tramos),
        truncada=False,
        nodos=[
            NodoRed(
                cuenta=cuenta,
                cuenta_id=None,
                externa=False,
                nivel=nivel,
                en_lista_riesgo=cuenta in listadas,
            )
            for cuenta, nivel in niveles.items()
        ],
        aristas=aristas,
        propuestas_lista_riesgo=[],
    )


CASO = [
    ("6033-9001", "6033-9002", 276_000),
    ("6033-9002", "6033-9003", 185_000),
    ("6033-9003", "7104-2288", 90_000),
]


def prueba_sin_aristas_no_hay_sankey() -> None:
    vacia = _red([("6033-9001", "6033-9002", 1)]).model_copy(update={"aristas": []})

    assert disponer_sankey(vacia) is None


def prueba_la_banda_mayor_mide_el_tope_y_el_resto_es_proporcional() -> None:
    sankey = disponer_sankey(_red(CASO))

    assert sankey is not None
    grosores = [b["grosor"] for b in sankey.bandas]
    assert grosores[0] == ALTO_BANDA_MAX
    for banda in sankey.bandas:
        esperado = banda["monto_crc"] * ALTO_BANDA_MAX / 276_000
        assert abs(banda["grosor"] - esperado) <= 0.5  # solo el redondeo a px entero
    assert grosores == sorted(grosores, reverse=True)  # el dinero se adelgaza


def prueba_el_dinero_no_pasa_por_flotantes_y_el_grosor_es_entero() -> None:
    sankey = disponer_sankey(_red(CASO))

    assert sankey is not None
    for banda in sankey.bandas:
        assert isinstance(banda["monto_crc"], int) and isinstance(banda["grosor"], int)


def prueba_una_banda_muy_chica_no_desaparece() -> None:
    assert grosor_px(1, 1_000_000_000) == GROSOR_MINIMO
    assert grosor_px(0, 100) == 0


def prueba_una_columna_por_salto_en_orden_y_con_el_paso_fijo() -> None:
    sankey = disponer_sankey(_red(CASO))

    assert sankey is not None
    assert sankey.saltos == 3 and sankey.columnas == 4
    assert [(n["columna"], n["cuenta"]) for n in sankey.nodos] == [
        (0, "6033-9001"), (1, "6033-9002"), (2, "6033-9003"), (3, "7104-2288"),
    ]
    xs = [n["x"] for n in sankey.nodos]
    assert [b - a for a, b in pairwise(xs)] == [COLUMNA_PASO] * 3
    assert [b["salto"] for b in sankey.bandas] == [1, 2, 3]
    assert sankey.ancho > xs[-1] + NODO_ANCHO


def prueba_una_cuenta_repetida_es_otro_nodo_en_otra_columna_y_una_sola_cuenta() -> None:
    # A -> B -> C -> B: B reaparece. No hay banda hacia atras.
    red = _red([("A-1", "B-2", 100_000), ("B-2", "C-3", 80_000), ("C-3", "B-2", 60_000)])
    sankey = disponer_sankey(red)

    assert sankey is not None
    assert [(n["columna"], n["cuenta"]) for n in sankey.nodos] == [
        (0, "A-1"), (1, "B-2"), (2, "C-3"), (3, "B-2"),
    ]
    assert [c["cuenta"] for c in sankey.cuentas] == ["A-1", "B-2", "C-3"]  # B una vez
    assert all(b["d"].startswith("M") for b in sankey.bandas)


def prueba_la_marca_de_lista_es_de_la_cuenta_y_sale_en_todos_sus_nodos() -> None:
    red = _red(
        [("A-1", "B-2", 100_000), ("B-2", "C-3", 80_000), ("C-3", "B-2", 60_000)],
        listadas=frozenset({"B-2"}),
    )
    sankey = disponer_sankey(red)

    assert sankey is not None
    marcados = [(n["columna"], n["en_lista"]) for n in sankey.nodos if n["cuenta"] == "B-2"]
    assert marcados == [(1, True), (3, True)]
    assert [c["en_lista"] for c in sankey.cuentas] == [False, True, False]


def prueba_las_bandas_de_un_nodo_no_se_salen_de_el_ni_se_pisan() -> None:
    """Dos salidas de la misma cuenta se apilan: el nodo es tan alto como la mayor suma."""
    base = _red([("A-1", "B-2", 200_000), ("A-1", "C-3", 100_000)])
    red = base.model_copy(
        update={"aristas": [base.aristas[0], base.aristas[1].model_copy(update={"salto": 1})]}
    )
    sankey = disponer_sankey(red)

    assert sankey is not None
    origen = sankey.nodos[0]
    assert origen["alto"] == sum(b["grosor"] for b in sankey.bandas)
    assert origen["alto"] == 140 + 70


def prueba_el_resumen_para_el_lector_de_pantalla_dice_saltos_y_montos() -> None:
    sankey = disponer_sankey(_red(CASO))

    assert sankey is not None
    assert "3 saltos" in sankey.resumen and "₡276.000" in sankey.resumen
    assert "₡90.000" in sankey.resumen and "CRC" not in sankey.resumen


VICTIMA = Victima(
    nombre="Maria Rodriguez Vargas",
    cuenta="8712-4455",
    transaccion_id="TXN-001",
    monto_crc=280_000,
    fecha=T0 - timedelta(minutes=4),
)


def prueba_con_victima_la_columna_0_es_el_cliente_y_la_primera_banda_su_transferencia() -> None:
    sankey = disponer_sankey(_red(CASO), VICTIMA)

    assert sankey is not None
    assert sankey.columnas == 5 and sankey.saltos == 3 and sankey.transferencias == 4
    assert [(n["columna"], n["cuenta"], n["victima"]) for n in sankey.nodos] == [
        (0, "8712-4455", True),
        (1, "6033-9001", False),
        (2, "6033-9002", False),
        (3, "6033-9003", False),
        (4, "7104-2288", False),
    ]
    montos = [b["monto_crc"] for b in sankey.bandas]
    assert montos == [280_000, 276_000, 185_000, 90_000]
    assert [b["salto"] for b in sankey.bandas] == [0, 1, 2, 3]
    assert sankey.bandas[0]["origen"] == "8712-4455" and sankey.bandas[0]["destino"] == "6033-9001"


def prueba_con_victima_el_grosor_es_proporcional_a_la_mayor_banda_que_es_la_de_ella() -> None:
    sankey = disponer_sankey(_red(CASO), VICTIMA)

    assert sankey is not None
    grosores = [b["grosor"] for b in sankey.bandas]
    assert grosores[0] == ALTO_BANDA_MAX
    for banda in sankey.bandas:
        assert abs(banda["grosor"] - banda["monto_crc"] * ALTO_BANDA_MAX / 280_000) <= 0.5
    assert grosores == sorted(grosores, reverse=True)


def prueba_la_victima_no_se_marca_ni_aparece_en_lista_aunque_su_cuenta_este_listada() -> None:
    red = _red(CASO, listadas=frozenset({"8712-4455", "6033-9001"}))
    sankey = disponer_sankey(red, VICTIMA)

    assert sankey is not None
    victima = sankey.nodos[0]
    assert victima["victima"] and victima["en_lista"] is False and victima["nombre"] == VICTIMA.nombre
    assert sankey.cuentas[0]["victima"] is True and sankey.cuentas[0]["en_lista"] is False
    assert [c["cuenta"] for c in sankey.cuentas if not c["victima"]] == [
        "6033-9001", "6033-9002", "6033-9003", "7104-2288",
    ]


def prueba_el_resumen_con_victima_nombra_a_la_victima_y_cuenta_cuatro_transferencias() -> None:
    sankey = disponer_sankey(_red(CASO), VICTIMA)

    assert sankey is not None
    assert sankey.resumen == (
        "Recorrido del dinero desde la víctima Maria Rodriguez Vargas: 4 transferencias, "
        "de ₡280.000 a ₡90.000 en la última"
    )


def prueba_si_la_cuenta_de_la_victima_reaparece_en_la_cadena_son_nodos_distintos() -> None:
    red = _red([("A-1", "B-2", 100_000), ("B-2", "C-3", 80_000), ("C-3", "A-1", 60_000)])
    sankey = disponer_sankey(red, Victima("Ana", "A-1", "TXN-001", 120_000, T0))

    assert sankey is not None
    assert [(n["columna"], n["cuenta"], n["victima"]) for n in sankey.nodos] == [
        (0, "A-1", True), (1, "A-1", False), (2, "B-2", False), (3, "C-3", False), (4, "A-1", False),
    ]
