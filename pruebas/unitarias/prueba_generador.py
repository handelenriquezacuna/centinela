"""H-03, H-04 y H-05: el generador de datos sinteticos, sin Mongo.

Lo que se verifica es lo que el equipo promete: misma semilla, mismos datos; ningun
monto por encima del tope de su entidad; fraude como minoria con etiqueta; los tres
patrones presentes; y perfiles coherentes con lo que el evaluador lee.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import timedelta
from functools import cache
from itertools import pairwise

import pytest
from pydantic import ValidationError

from app.generador.ejecucion import Conjunto, generar_conjunto
from app.generador.fraude import CADENA, MONTO_ATIPICO, PATRONES, RAFAGA
from app.generador.transacciones import limite_de_la_historia
from app.modelos import Cliente, Cuenta, PerfilComportamiento, Transaccion
from app.nucleo.config import AjustesGenerador, EntidadSinpe

# Margen de R-01 (diez veces el promedio): aqui no se lee de Mongo, es el umbral de negocio
# del catalogo y la prueba solo comprueba que lo legitimo queda lejos y lo atipico, encima.
DIEZ_VECES = 10


def ajustes(**cambios) -> AjustesGenerador:
    return AjustesGenerador(clientes=200, transacciones=5000, **cambios)


@cache
def conjunto_base() -> Conjunto:
    return generar_conjunto(ajustes())


def topes() -> dict[str, int]:
    """tope por cuenta_id, sacado de la entidad de la cuenta y de la lista configurada."""
    por_entidad = {e.nombre: e.tope_crc for e in ajustes().entidades}
    return {c["_id"]: por_entidad[c["entidad"]] for c in conjunto_base().cuentas}


def perfiles_por_cuenta() -> dict[str, dict]:
    cliente_a_cuenta = {c["cliente_id"]: c["_id"] for c in conjunto_base().cuentas}
    return {cliente_a_cuenta[p["_id"]]: p for p in conjunto_base().perfiles}


def episodios() -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = defaultdict(list)
    for txn in conjunto_base().transacciones:
        if txn["simulacion"]["fraude"]:
            grupos[txn["simulacion"]["episodio"]].append(txn)
    return grupos


# ---------------------------------------------------------------------------
# H-03: poblacion y determinismo
# ---------------------------------------------------------------------------


def prueba_misma_semilla_mismos_datos() -> None:
    primero = generar_conjunto(ajustes(semilla=7))
    segundo = generar_conjunto(ajustes(semilla=7))

    assert primero.clientes == segundo.clientes
    assert primero.cuentas == segundo.cuentas
    assert primero.perfiles == segundo.perfiles
    assert primero.transacciones == segundo.transacciones


def prueba_otra_semilla_otros_datos() -> None:
    otro = generar_conjunto(ajustes(semilla=8))
    assert otro.transacciones != generar_conjunto(ajustes(semilla=7)).transacciones


def prueba_genera_los_clientes_pedidos_con_una_cuenta_cada_uno() -> None:
    conjunto = conjunto_base()

    assert len(conjunto.clientes) == 200
    assert len(conjunto.cuentas) == 200
    assert len(conjunto.perfiles) == 200
    assert {c["cliente_id"] for c in conjunto.cuentas} == {c["_id"] for c in conjunto.clientes}
    # Los indices unicos de la base: ni cedula ni cuenta SINPE se repiten.
    assert len({c["cedula"] for c in conjunto.clientes}) == 200
    assert len({c["cuenta_sinpe"] for c in conjunto.cuentas}) == 200


def prueba_los_identificadores_pasan_de_tres_digitos_y_siguen_el_contrato() -> None:
    conjunto = conjunto_base()

    assert conjunto.clientes[0]["_id"] == "CLI-0001"
    assert conjunto.cuentas[0]["_id"] == "CTA-0001"
    assert conjunto.transacciones[0]["_id"] == "TXN-000001"
    for documento in conjunto.clientes:
        assert re.fullmatch(r"CLI-\d{3,}", documento["_id"])
    for documento in conjunto.transacciones:
        assert re.fullmatch(r"TXN-\d{3,}", documento["_id"])
        assert re.fullmatch(r"CTA-\d{3,}", documento["cuenta_origen"])


def prueba_cada_cuenta_lleva_una_entidad_de_la_configuracion() -> None:
    nombres = {e.nombre for e in ajustes().entidades}
    usadas = {c["entidad"] for c in conjunto_base().cuentas}

    assert usadas <= nombres
    assert len(usadas) >= 4  # con 200 cuentas aparecen todas las entidades principales


def prueba_los_perfiles_son_coherentes_con_el_historial_legitimo() -> None:
    conjunto = conjunto_base()
    legitimas: dict[str, list[int]] = defaultdict(list)
    for txn in conjunto.transacciones:
        if not txn["simulacion"]["fraude"]:
            legitimas[txn["cuenta_origen"]].append(txn["monto_crc"])
    tope = topes()

    for cuenta_id, perfil in perfiles_por_cuenta().items():
        montos = legitimas[cuenta_id]
        assert perfil["num_transferencias"] == len(montos)
        assert perfil["maximo_crc"] == max(montos)
        assert perfil["promedio_crc"] == round(statistics.fmean(montos))
        assert perfil["maximo_crc"] <= tope[cuenta_id]
        assert 0 < perfil["promedio_crc"] <= perfil["maximo_crc"]
        assert perfil["destinos_frecuentes"]
        # Lo legitimo queda lejos de lo que R-01 considera atipico.
        assert perfil["maximo_crc"] < DIEZ_VECES * perfil["promedio_crc"]


def prueba_el_perfil_entra_al_modelo_que_lee_el_evaluador() -> None:
    for documento in conjunto_base().perfiles:
        modelo = PerfilComportamiento.model_validate(documento)
        assert modelo.horario_habitual.inicio != ""
        assert modelo.promedio_crc > 0


def prueba_los_documentos_pasan_los_modelos_de_dominio() -> None:
    conjunto = conjunto_base()
    for documento in conjunto.clientes:
        Cliente.model_validate(documento)
    for documento in conjunto.cuentas:
        Cuenta.model_validate(documento)
    for documento in conjunto.transacciones:
        Transaccion.model_validate(documento)


def prueba_los_destinos_frecuentes_son_propios_de_cada_cliente() -> None:
    perfiles = conjunto_base().perfiles
    conjuntos = {tuple(sorted(p["destinos_frecuentes"])) for p in perfiles}

    assert len(conjuntos) > len(perfiles) * 0.9


# ---------------------------------------------------------------------------
# H-04: transacciones legitimas
# ---------------------------------------------------------------------------


def prueba_el_total_es_el_pedido_y_el_dinero_es_entero() -> None:
    transacciones = conjunto_base().transacciones

    assert len(transacciones) == 5000
    for txn in transacciones:
        assert type(txn["monto_crc"]) is int
        assert txn["monto_crc"] >= 1
        assert txn["moneda"] == "CRC"
        assert txn["esquema_version"] == 1
        assert txn["fecha"].utcoffset().total_seconds() == 0


def prueba_ningun_monto_supera_el_tope_de_la_entidad_de_origen() -> None:
    tope = topes()
    excedidas = [
        t["_id"] for t in conjunto_base().transacciones if t["monto_crc"] > tope[t["cuenta_origen"]]
    ]

    assert excedidas == []


def prueba_el_tope_sale_de_la_configuracion_y_no_del_codigo() -> None:
    estrecho = AjustesGenerador(
        clientes=50,
        transacciones=2000,
        entidades=[
            EntidadSinpe(nombre=f"E{n}", tope_crc=100_000) for n in range(4)
        ],
    )
    conjunto = generar_conjunto(estrecho)

    assert max(t["monto_crc"] for t in conjunto.transacciones) <= 100_000


def prueba_los_ids_siguen_el_orden_cronologico() -> None:
    fechas = [t["fecha"] for t in conjunto_base().transacciones]

    assert fechas == sorted(fechas)


def prueba_la_historia_cae_dentro_de_la_ventana_configurada() -> None:
    parametros = ajustes()
    fechas = [t["fecha"] for t in conjunto_base().transacciones]

    limite = limite_de_la_historia(parametros)
    assert max(fechas) < limite
    assert min(fechas) >= limite - timedelta(days=parametros.dias_historia)


def prueba_las_horas_siguen_la_forma_de_un_dia_real() -> None:
    horas = Counter(
        (t["fecha"].hour - 6) % 24
        for t in conjunto_base().transacciones
        if not t["simulacion"]["fraude"]
    )
    madrugada = sum(horas[h] for h in (0, 1, 2, 3, 4))
    mediodia = sum(horas[h] for h in (11, 12, 13))

    assert mediodia > 5 * max(madrugada, 1)


def prueba_lo_legitimo_respeta_el_horario_habitual_casi_siempre() -> None:
    conjunto = conjunto_base()
    perfiles = perfiles_por_cuenta()
    dentro = total = 0
    for txn in conjunto.transacciones:
        if txn["simulacion"]["fraude"]:
            continue
        perfil = perfiles[txn["cuenta_origen"]]
        inicio = int(perfil["horario_habitual"]["inicio"][:2])
        fin = int(perfil["horario_habitual"]["fin"][:2])
        hora = (txn["fecha"].hour - 6) % 24
        habitual = inicio <= hora < fin if inicio <= fin else (hora >= inicio or hora < fin)
        total += 1
        dentro += habitual

    assert dentro / total > 0.95


def prueba_faltan_transacciones_para_los_clientes_es_un_error_claro() -> None:
    with pytest.raises(ValueError, match="no alcanzan"):
        generar_conjunto(AjustesGenerador(clientes=500, transacciones=1000))


# ---------------------------------------------------------------------------
# H-05: fraude
# ---------------------------------------------------------------------------


def prueba_el_fraude_es_minoria_y_menos_del_dos_por_ciento() -> None:
    conjunto = conjunto_base()

    assert 0 < conjunto.total_fraude < 0.02 * len(conjunto.transacciones)


def prueba_cada_patron_aparece() -> None:
    por_patron = conjunto_base().fraude_por_patron

    assert set(por_patron) == set(PATRONES)
    assert all(cantidad >= 1 for cantidad in por_patron.values())


def prueba_toda_transaccion_lleva_la_etiqueta_y_solo_el_fraude_lleva_patron() -> None:
    for txn in conjunto_base().transacciones:
        etiqueta = txn["simulacion"]
        assert isinstance(etiqueta["fraude"], bool)
        if etiqueta["fraude"]:
            assert etiqueta["patron"] in PATRONES
            assert re.fullmatch(r"EPI-\d{3,}", etiqueta["episodio"])
        else:
            assert set(etiqueta) == {"fraude"}


def prueba_el_monto_atipico_es_un_multiplo_del_promedio_y_cabe_en_el_tope() -> None:
    perfiles = perfiles_por_cuenta()
    tope = topes()
    atipicas = [
        txn
        for grupo in episodios().values()
        for txn in grupo
        if txn["simulacion"]["patron"] == MONTO_ATIPICO
    ]

    assert atipicas
    for txn in atipicas:
        promedio = perfiles[txn["cuenta_origen"]]["promedio_crc"]
        assert txn["monto_crc"] > DIEZ_VECES * promedio
        assert txn["monto_crc"] <= tope[txn["cuenta_origen"]]


def prueba_el_destino_del_fraude_es_nuevo_para_la_victima() -> None:
    perfiles = perfiles_por_cuenta()
    historial: dict[str, set[str]] = defaultdict(set)
    for txn in conjunto_base().transacciones:
        if not txn["simulacion"]["fraude"]:
            historial[txn["cuenta_origen"]].add(txn["cuenta_destino"])

    for grupo in episodios().values():
        primera = grupo[0]
        assert primera["cuenta_destino"] not in historial[primera["cuenta_origen"]]
        assert primera["cuenta_destino"] not in perfiles[primera["cuenta_origen"]][
            "destinos_frecuentes"
        ]


def prueba_la_rafaga_son_varias_transferencias_seguidas_al_mismo_destino() -> None:
    rafagas = [
        grupo
        for grupo in episodios().values()
        if grupo[0]["simulacion"]["patron"] == RAFAGA
    ]
    tope = topes()

    assert rafagas
    for grupo in rafagas:
        assert 4 <= len(grupo) <= 6
        assert len({t["cuenta_origen"] for t in grupo}) == 1
        assert len({t["cuenta_destino"] for t in grupo}) == 1
        assert (grupo[-1]["fecha"] - grupo[0]["fecha"]).total_seconds() <= 30 * 60
        assert all(t["monto_crc"] <= tope[t["cuenta_origen"]] for t in grupo)
        assert sum(t["monto_crc"] for t in grupo) >= 300_000


def prueba_la_cadena_de_mulas_encadena_cada_destino_con_el_siguiente_origen() -> None:
    sinpe_de = {c["_id"]: c["cuenta_sinpe"] for c in conjunto_base().cuentas}
    cadenas = [
        grupo
        for grupo in episodios().values()
        if grupo[0]["simulacion"]["patron"] == CADENA
    ]

    assert cadenas
    for grupo in cadenas:
        assert 4 <= len(grupo) <= 5
        for eslabon, siguiente in pairwise(grupo):
            assert eslabon["cuenta_destino"] == sinpe_de[siguiente["cuenta_origen"]]
            assert siguiente["fecha"] > eslabon["fecha"]
            assert siguiente["monto_crc"] <= eslabon["monto_crc"]
        assert len({t["cuenta_origen"] for t in grupo}) == len(grupo)


def prueba_la_proporcion_de_dos_por_ciento_o_mas_no_se_acepta() -> None:
    with pytest.raises(ValidationError):
        AjustesGenerador(proporcion_anomalos=0.02)


def prueba_la_configuracion_exige_al_menos_cuatro_entidades_y_topes_de_sinpe() -> None:
    cuatro = [EntidadSinpe(nombre=f"E{n}", tope_crc=100_000) for n in range(4)]

    with pytest.raises(ValidationError):
        AjustesGenerador(entidades=cuatro[:3])
    with pytest.raises(ValidationError):
        EntidadSinpe(nombre="X", tope_crc=400_000)
    with pytest.raises(ValidationError):
        AjustesGenerador(entidades=[*cuatro[:3], cuatro[0]])
    with pytest.raises(ValidationError):
        AjustesGenerador(semilla_rara=1)


# ---------------------------------------------------------------------------
# Mulas reportadas en listas_riesgo
# ---------------------------------------------------------------------------


def _mulas_que_reciben() -> dict[str, object]:
    sinpe_mulas = {
        c["cuenta_sinpe"]
        for c in conjunto_base().cuentas
        if c["_id"] in set(conjunto_base().poblacion.cuentas_mula)
    }
    primera: dict[str, object] = {}
    for txn in conjunto_base().transacciones:
        if txn["simulacion"].get("patron") == CADENA and txn["cuenta_destino"] in sinpe_mulas:
            primera.setdefault(txn["cuenta_destino"], txn["fecha"])
    return primera


def prueba_se_reporta_la_fraccion_pedida_de_las_mulas_de_las_cadenas() -> None:
    recibidas = _mulas_que_reciben()
    reportadas = conjunto_base().listas_riesgo

    assert len(reportadas) == round(len(recibidas) * 0.5)
    assert {r["cuenta"] for r in reportadas} <= set(recibidas)
    assert len({r["cuenta"] for r in reportadas}) == len(reportadas)
    assert len(generar_conjunto(ajustes(proporcion_mulas_reportadas=1)).listas_riesgo) == len(
        recibidas
    )
    assert generar_conjunto(ajustes(proporcion_mulas_reportadas=0)).listas_riesgo == []


def prueba_el_reporte_es_vigente_y_anterior_a_la_primera_transaccion_recibida() -> None:
    recibidas = _mulas_que_reciben()

    assert conjunto_base().listas_riesgo
    for entrada in conjunto_base().listas_riesgo:
        assert entrada["vigente"] is True
        assert entrada["fecha_reporte"] < recibidas[entrada["cuenta"]]
        assert re.fullmatch(r"RSK-\d{4,}", entrada["_id"])


def prueba_las_mulas_reportadas_son_deterministas_y_pasan_el_modelo() -> None:
    from app.modelos import EntradaListaRiesgo

    otra = generar_conjunto(ajustes())

    assert otra.listas_riesgo == conjunto_base().listas_riesgo
    for entrada in otra.listas_riesgo:
        EntradaListaRiesgo.model_validate(entrada)


def prueba_la_proporcion_de_mulas_reportadas_va_de_cero_a_uno() -> None:
    with pytest.raises(ValidationError):
        AjustesGenerador(proporcion_mulas_reportadas=1.5)
    with pytest.raises(ValidationError):
        AjustesGenerador(proporcion_mulas_reportadas=-0.1)
