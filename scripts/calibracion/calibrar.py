"""Calibracion de las reglas de deteccion contra el generador (H-10, T-33).

Busca pesos, umbrales y parametros de las reglas que suban el F1 del motor sin bajar la
exhaustividad. Es un experimento fuera de `app/`: genera los datos en memoria con
`app.generador`, arma el contexto de cada transaccion como lo hace D1
(`app/repos/deteccion.py`) y puntua con el MISMO evaluador (`app.deteccion.evaluador`).
No toca Mongo.

Metodologia, para no sobreajustar:

1. Se calibra con la semilla del YAML y se valida con otras, que la busqueda nunca ve. Una
   configuracion solo gana si mejora el F1 en validacion y su exhaustividad no baja del
   piso (90 %) en NINGUN conjunto de validacion. Cada semilla se corre en dos mundos: el
   de las pruebas de H-10 (4 entidades bancarias) y el de config/centinela.yml (5).
2. La rejilla es chica y solo toca parametros con sentido de negocio. Los pesos de
   R-01, R-03 y R-05 NO entran: el caso documentado (35 + 25 + 15 = 75, CRITICA) depende
   de ellos. R-11 no entra en la rejilla principal: se evalua aparte con `--r11` (T-63),
   encima de la ganadora, con los rasgos de grafo que D3 materializa en linea.
3. A igual F1 de calibracion gana la configuracion que cambia menos parametros.

La definicion de metricas es la de H-10 (`app/deteccion/efectividad.py`): una
transaccion etiquetada cuenta como detectada si el motor le levanta alerta (puntaje >=
umbral_alerta y con severidad), sin importar el estado posterior de la alerta.

Uso:

    uv run python scripts/calibracion/calibrar.py
    uv run python scripts/calibracion/calibrar.py --r11     # experimento de R-11 (T-63)
"""

from __future__ import annotations

import argparse
import itertools
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.deteccion.evaluador import (
    ContextoEvaluacion,
    PoliticaDeteccion,
    RasgoRed,
    VentanaOrigen,
    evaluar,
)
from app.deteccion.rasgos_grafo import calcular_rasgo
from app.generador.ejecucion import generar_conjunto
from app.modelos import (
    EntradaListaRiesgo,
    PerfilComportamiento,
    Regla,
    Transaccion,
)
from app.nucleo.config import AjustesGenerador, obtener_ajustes

PISO_EXHAUSTIVIDAD = 0.90
SEMILLAS_VALIDACION = (7, 1234, 98765, 20260501)
DIAS_HISTORIAL = 90  # parametro de R-03; la historia generada dura 30 dias

# ---------------------------------------------------------------------------
# Configuracion candidata
# ---------------------------------------------------------------------------

# (codigo, _id, tipo, umbral, parametros, peso, activa)
FilaRegla = tuple[str, str, str, float, tuple[tuple[str, int | float], ...], int, bool]

BASE_REGLAS: tuple[FilaRegla, ...] = (
    ("R-01", "REG-001", "monto", 10, (), 35, True),
    ("R-03", "REG-002", "destino", 0, (("dias_historial", DIAS_HISTORIAL),), 25, True),
    ("R-05", "REG-003", "horario", 0, (), 15, True),
    ("R-07", "REG-004", "lista_riesgo", 1, (), 40, True),
    (
        "R-09",
        "REG-005",
        "velocidad",
        3,
        (("ventana_minutos", 30), ("monto_acumulado_crc", 300_000)),
        30,
        True,
    ),
    ("R-11", "REG-006", "red_mulas", 0.8, (("ventana_minutos", 15), ("saltos_minimos", 2)), 20, False),
)


@dataclass(frozen=True)
class Configuracion:
    """Lo que se calibra: las reglas y el umbral de alerta. Los cortes de severidad se
    derivan: el primero arranca en `umbral_alerta` (lo exige 03-catalogos.js)."""

    nombre: str
    umbral_alerta: int
    reglas: tuple[FilaRegla, ...]

    def regla(self, codigo: str) -> FilaRegla:
        return next(fila for fila in self.reglas if fila[0] == codigo)

    def con(
        self, nombre: str, umbral_alerta: int | None = None, **cambios: dict
    ) -> Configuracion:
        """Copia con cambios por codigo: `con("x", R_07={"peso": 30})` (guion bajo)."""
        filas = []
        for codigo, regla_id, tipo, umbral, parametros, peso, activa in self.reglas:
            cambio = cambios.get(codigo.replace("-", "_"), {})
            filas.append(
                (
                    codigo,
                    regla_id,
                    tipo,
                    cambio.get("umbral", umbral),
                    tuple(cambio.get("parametros", parametros)),
                    cambio.get("peso", peso),
                    cambio.get("activa", activa),
                )
            )
        return Configuracion(
            nombre, self.umbral_alerta if umbral_alerta is None else umbral_alerta, tuple(filas)
        )

    def diferencias(self, otra: Configuracion) -> list[str]:
        cambios = []
        if self.umbral_alerta != otra.umbral_alerta:
            cambios.append(f"umbral_alerta {otra.umbral_alerta} -> {self.umbral_alerta}")
        for mia, base in zip(self.reglas, otra.reglas, strict=True):
            if mia[3] != base[3]:
                cambios.append(f"{mia[0]} umbral {base[3]} -> {mia[3]}")
            if mia[5] != base[5]:
                cambios.append(f"{mia[0]} peso {base[5]} -> {mia[5]}")
            if mia[4] != base[4]:
                cambios.append(f"{mia[0]} parametros {dict(base[4])} -> {dict(mia[4])}")
            if mia[6] != base[6]:
                cambios.append(f"{mia[0]} activa {base[6]} -> {mia[6]}")
        return cambios


BASE = Configuracion("base", 40, BASE_REGLAS)

# Ganadora de la rejilla principal (T-33): lo que cambia R-01, R-05 y el umbral de alerta.
# R-11 no entra en la rejilla; se evalua aparte, encima de esta (`--r11`).
GANADORA_REJILLA = BASE.con(
    "ganadora de la rejilla",
    35,
    R_01={"umbral": 8},
    R_05={"umbral": 120},
)

# Lo que quedo aplicado en scripts/semilla/03-catalogos.js: la de la rejilla (T-33) y R-11
# encendida con los parametros que la semilla ya declaraba (T-63).
# `pruebas/unitarias/prueba_calibracion.py` verifica que la semilla dice exactamente esto.
GANADORA = GANADORA_REJILLA.con("ganadora", R_11={"activa": True})


def reglas_de(config: Configuracion) -> list[Regla]:
    return [
        Regla.model_validate(
            {
                "_id": regla_id,
                "codigo": codigo,
                "descripcion": codigo,
                "tipo": tipo,
                "umbral": umbral,
                "parametros": dict(parametros),
                "peso": peso,
                "activa": activa,
                "version": 1,
                "esquema_version": 1,
            }
        )
        for codigo, regla_id, tipo, umbral, parametros, peso, activa in config.reglas
    ]


def politica_de(config: Configuracion) -> PoliticaDeteccion:
    minimo = config.umbral_alerta
    return PoliticaDeteccion.model_validate(
        {
            "_id": "POL-CAL",
            "version": 1,
            "umbral_alerta": minimo,
            "puntaje_maximo": 100,
            "cortes_severidad": [
                {"severidad": "MEDIA", "puntaje_minimo": minimo, "puntaje_maximo": 54},
                {"severidad": "ALTA", "puntaje_minimo": 55, "puntaje_maximo": 69},
                {"severidad": "CRITICA", "puntaje_minimo": 70, "puntaje_maximo": 100},
            ],
        }
    )


# ---------------------------------------------------------------------------
# Datos en memoria y contexto (la misma semantica que RepoDeteccion.armar_contexto)
# ---------------------------------------------------------------------------


HORIZONTE_HORAS = 24  # el de config/centinela.yml, rasgos_grafo.horizonte_horas


class _VistaAlCorte:
    """`indice.get(cuenta)` restringido a `[desde, corte)`, con bisect sobre lo ya ordenado.

    Lo justo de `Mapping` que usa `calcular_rasgo`, para no reindexar 5.000 veces.
    """

    def __init__(self, indice: dict, desde, corte) -> None:
        self._indice, self._desde, self._corte = indice, desde, corte

    def get(self, cuenta: str, por_omision=()):
        if cuenta not in self._indice:
            return por_omision
        fechas, txns = self._indice[cuenta]
        return txns[bisect_left(fechas, self._desde) : bisect_left(fechas, self._corte)]


@dataclass
class Datos:
    semilla: int
    mundo: str  # "defecto" o "yaml"
    transacciones: list[Transaccion]
    etiquetas: list[tuple[bool, str | None]]  # (fraude, patron)
    perfiles: dict[str, PerfilComportamiento]
    cliente_de_cuenta: dict[str, str]
    listas: dict[str, list[EntradaListaRiesgo]]
    _fechas_destino: dict[tuple[str, str], list] = field(default_factory=dict)
    _fechas_origen: dict[str, list] = field(default_factory=dict)
    _acumulado_origen: dict[str, list[int]] = field(default_factory=dict)
    _contextos: dict[tuple[int, int, int | None], list[ContextoEvaluacion]] = field(
        default_factory=dict
    )
    _rasgos: dict[int, list[RasgoRed]] = field(default_factory=dict)
    _recibos: dict[str, tuple[list, list[Transaccion]]] = field(default_factory=dict)
    _salidas: dict[str, tuple[list, list[Transaccion]]] = field(default_factory=dict)

    def indexar(self) -> None:
        por_destino: dict[tuple[str, str], list] = defaultdict(list)
        por_origen: dict[str, list] = defaultdict(list)
        montos: dict[str, list[int]] = defaultdict(list)
        for txn in self.transacciones:  # ya cronologico
            cliente = self.cliente_de_cuenta[txn.cuenta_origen]
            por_destino[(cliente, txn.cuenta_destino)].append(txn.fecha)
            por_origen[txn.cuenta_origen].append(txn.fecha)
            montos[txn.cuenta_origen].append(txn.monto_crc)
        recibos: dict[str, list[Transaccion]] = defaultdict(list)
        salidas: dict[str, list[Transaccion]] = defaultdict(list)
        for txn in self.transacciones:
            recibos[txn.cuenta_destino].append(txn)
            if txn.sinpe_origen is not None:
                salidas[txn.sinpe_origen].append(txn)
        self._recibos = {c: ([t.fecha for t in ts], ts) for c, ts in recibos.items()}
        self._salidas = {c: ([t.fecha for t in ts], ts) for c, ts in salidas.items()}
        self._fechas_destino = dict(por_destino)
        self._fechas_origen = dict(por_origen)
        self._acumulado_origen = {
            cuenta: [0, *itertools.accumulate(lista)] for cuenta, lista in montos.items()
        }

    def rasgos(self, ventana_minutos: int, horas: int = HORIZONTE_HORAS) -> list[RasgoRed]:
        """El rasgo del destino de cada transaccion, tal como lo tendria D3 un instante
        antes de que llegue: `[fecha - horas, fecha)`, sin la propia ni lo posterior.

        Es `calcular_rasgo`, la definicion que el pipeline de Mongo reproduce (una prueba de
        integracion las compara). Aqui el rango se desliza con cada transaccion, que es el
        mejor caso para R-11: D3 recalcula cada pocos segundos, no en cada evento.
        """
        if ventana_minutos in self._rasgos:
            return self._rasgos[ventana_minutos]
        from datetime import timedelta

        recibos, salidas = self._recibos, self._salidas
        resultado = []
        for txn in self.transacciones:
            desde = txn.fecha - timedelta(hours=horas)
            corte = txn.fecha
            resultado.append(
                calcular_rasgo(
                    txn.cuenta_destino,
                    _VistaAlCorte(recibos, desde, corte),
                    _VistaAlCorte(salidas, desde, corte),
                    desde=desde,
                    corte=corte,
                    ventana_minutos=ventana_minutos,
                    vigencia_segundos=60,
                )
            )
        self._rasgos[ventana_minutos] = resultado
        return resultado

    def contextos(
        self, dias: int, minutos: int, ventana_rasgo: int | None = None
    ) -> list[ContextoEvaluacion]:
        """Un contexto por transaccion, con ventanas hacia atras desde su fecha."""
        clave = (dias, minutos, ventana_rasgo)
        if clave in self._contextos:
            return self._contextos[clave]
        from datetime import timedelta

        resultado = []
        for txn in self.transacciones:
            cliente = self.cliente_de_cuenta[txn.cuenta_origen]
            fechas = self._fechas_destino[(cliente, txn.cuenta_destino)]
            # `fecha >= f - dias` y `fecha < f`, sin la propia (su fecha es f, queda fuera).
            previas = bisect_left(fechas, txn.fecha) - bisect_left(
                fechas, txn.fecha - timedelta(days=dias)
            )
            fechas_o = self._fechas_origen[txn.cuenta_origen]
            acumulado = self._acumulado_origen[txn.cuenta_origen]
            izquierda = bisect_left(fechas_o, txn.fecha - timedelta(minutes=minutos))
            derecha = bisect_right(fechas_o, txn.fecha)
            # `fecha` en [f - minutos, f]; la propia esta dentro y se cuenta una sola vez.
            cantidad = derecha - izquierda
            monto = acumulado[derecha] - acumulado[izquierda]
            resultado.append(
                ContextoEvaluacion(
                    perfil=self.perfiles.get(cliente),
                    dias_historial=dias,
                    destinos_conocidos={txn.cuenta_destino: previas},
                    entradas_lista_riesgo=tuple(self.listas.get(txn.cuenta_destino, ())),
                    ventana_origen=VentanaOrigen(
                        ventana_minutos=minutos, cantidad=cantidad, monto_acumulado_crc=monto
                    ),
                )
            )
        if ventana_rasgo is not None:
            rasgos = self.rasgos(ventana_rasgo)
            resultado = [
                contexto.model_copy(update={"rasgo_red": rasgo})
                for contexto, rasgo in zip(resultado, rasgos, strict=True)
            ]
        self._contextos[clave] = resultado
        return resultado


def _a_milisegundos(fecha):
    return fecha.replace(microsecond=fecha.microsecond // 1000 * 1000)


def generar_datos(semilla: int, *, entidades_del_yaml: bool = False) -> Datos:
    """Conjunto de la semilla dada.

    Por omision, el mismo que usa la medicion de H-10 en pruebas: `AjustesGenerador(
    clientes=200, transacciones=5000)` con los valores por omision del modelo (4 entidades
    bancarias). Con `entidades_del_yaml`, el de `config/centinela.yml` (5 entidades): un
    mundo ligeramente distinto para comprobar que la calibracion no depende de el.
    """
    if entidades_del_yaml:
        base = obtener_ajustes().generador.model_dump()
    else:
        base = AjustesGenerador(clientes=200, transacciones=5000).model_dump()
    ajustes = AjustesGenerador.model_validate({**base, "semilla": semilla})
    conjunto = generar_conjunto(ajustes)
    # Mongo guarda las fechas con precision de milisegundo: se trunca igual para que una
    # frontera de ventana caiga del mismo lado que en la base.
    for doc in (*conjunto.transacciones, *conjunto.listas_riesgo):
        for campo in ("fecha", "fecha_reporte"):
            if campo in doc:
                doc[campo] = _a_milisegundos(doc[campo])
    datos = Datos(
        semilla=semilla,
        mundo="yaml" if entidades_del_yaml else "defecto",
        transacciones=[Transaccion.model_validate(doc) for doc in conjunto.transacciones],
        etiquetas=[
            (doc["simulacion"]["fraude"], doc["simulacion"].get("patron"))
            for doc in conjunto.transacciones
        ],
        perfiles={
            doc["_id"]: PerfilComportamiento.model_validate(doc) for doc in conjunto.perfiles
        },
        cliente_de_cuenta={doc["_id"]: doc["cliente_id"] for doc in conjunto.cuentas},
        listas={},
    )
    listas: dict[str, list[EntradaListaRiesgo]] = defaultdict(list)
    for doc in conjunto.listas_riesgo:
        listas[doc["cuenta"]].append(EntradaListaRiesgo.model_validate(doc))
    datos.listas = dict(listas)
    datos.indexar()
    return datos


# ---------------------------------------------------------------------------
# Metricas (definicion de H-10)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Resultado:
    detectados: int
    perdidos: int
    falsos_positivos: int
    por_patron: tuple[tuple[str, int, int], ...]  # (patron, fraudes, detectados)

    @property
    def precision(self) -> float:
        total = self.detectados + self.falsos_positivos
        return self.detectados / total if total else 0.0

    @property
    def exhaustividad(self) -> float:
        total = self.detectados + self.perdidos
        return self.detectados / total if total else 0.0

    @property
    def f1(self) -> float:
        p, e = self.precision, self.exhaustividad
        return 2 * p * e / (p + e) if p + e else 0.0


def resultado_de(conteos: Counter) -> Resultado:
    """`conteos[(fraude, patron, alertada)] -> transacciones`."""
    detectados = sum(n for (fraude, _, alerta), n in conteos.items() if fraude and alerta)
    perdidos = sum(n for (fraude, _, alerta), n in conteos.items() if fraude and not alerta)
    falsos = sum(n for (fraude, _, alerta), n in conteos.items() if not fraude and alerta)
    patrones: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for (fraude, patron, alerta), n in conteos.items():
        if fraude:
            patrones[patron or "sin_patron"][0] += n
            patrones[patron or "sin_patron"][1] += n if alerta else 0
    return Resultado(
        detectados,
        perdidos,
        falsos,
        tuple((p, v[0], v[1]) for p, v in sorted(patrones.items())),
    )


def medir(config: Configuracion, datos: Datos) -> Resultado:
    """Evalua toda la configuracion con el evaluador real, transaccion por transaccion."""
    reglas = reglas_de(config)
    politica = politica_de(config)
    contextos = datos.contextos(*_ventanas(config), _ventana_rasgo(config))
    conteos: Counter = Counter()
    for txn, contexto, (fraude, patron) in zip(
        datos.transacciones, contextos, datos.etiquetas, strict=True
    ):
        evaluacion = evaluar(txn, contexto, reglas, politica)
        alerta = evaluacion.genera_alerta and evaluacion.severidad is not None
        conteos[(fraude, patron, alerta)] += 1
    return resultado_de(conteos)


def _ventana_rasgo(config: Configuracion) -> int | None:
    """Ventana de R-11 si esta activa en la configuracion; sin R-11 no se arman rasgos."""
    fila = config.regla("R-11")
    return int(dict(fila[4])["ventana_minutos"]) if fila[6] else None


def _ventanas(config: Configuracion) -> tuple[int, int]:
    dias = int(dict(config.regla("R-03")[4])["dias_historial"])
    minutos = int(dict(config.regla("R-09")[4])["ventana_minutos"])
    return dias, minutos


# ---------------------------------------------------------------------------
# Busqueda
# ---------------------------------------------------------------------------

# Rejilla pequena. Cada entrada es una perilla con sentido de negocio.
UMBRALES_R01 = (8, 10, 12)
UMBRALES_R03 = (0, 1)
HOLGURAS_R05 = (0, 60, 120)  # minutos fuera del horario habitual
UMBRALES_R09 = (2, 3, 4)  # transferencias minimas en la ventana
VENTANAS_R09 = (15, 30, 60)
MONTOS_R09 = (150_000, 300_000, 450_000)
PESOS_R07 = (30, 40, 50)
PESOS_R09 = (20, 30, 40)
UMBRALES_ALERTA = (35, 40, 45, 50)

_BITS = {"R-01": 1, "R-03": 2, "R-05": 4, "R-07": 8, "R-09": 16}


def _bandera(config: Configuracion, codigo: str, datos: Datos) -> list[int]:
    """Por transaccion: el bit de la regla si dispara, 0 si no. Usa `evaluar` con SOLO
    esa regla, asi que es el evaluador real, no una reimplementacion."""
    solo = Configuracion("solo", 0, tuple(f for f in config.reglas if f[0] == codigo))
    # Para R-09 y R-03 el contexto se arma con los parametros de ESA regla.
    dias = DIAS_HISTORIAL
    minutos = 30
    fila = config.regla(codigo)
    if codigo == "R-03":
        dias = int(dict(fila[4])["dias_historial"])
    if codigo == "R-09":
        minutos = int(dict(fila[4])["ventana_minutos"])
    contextos = datos.contextos(dias, minutos)
    reglas = reglas_de(solo)
    politica = politica_de(BASE)
    bit = _BITS[codigo]
    return [
        bit if evaluar(txn, contexto, reglas, politica).reglas_disparadas else 0
        for txn, contexto in zip(datos.transacciones, contextos, strict=True)
    ]


def _fila(config: Configuracion, codigo: str, **cambio) -> FilaRegla:
    return config.con("x", **{codigo.replace("-", "_"): cambio}).regla(codigo)


def candidatas() -> list[Configuracion]:
    """Producto de la rejilla. ~700 combinaciones de reglas x pesos x umbral_alerta."""
    resultado = []
    for u1, u3, h5, u9, v9, m9 in itertools.product(
        UMBRALES_R01, UMBRALES_R03, HOLGURAS_R05, UMBRALES_R09, VENTANAS_R09, MONTOS_R09
    ):
        config = BASE.con(
            "cand",
            R_01={"umbral": u1},
            R_03={"umbral": u3},
            R_05={"umbral": h5},
            R_09={
                "umbral": u9,
                "parametros": (("ventana_minutos", v9), ("monto_acumulado_crc", m9)),
            },
        )
        resultado.append(config)
    return resultado


def buscar(datos: Datos) -> list[tuple[Configuracion, Resultado]]:
    """Todas las candidatas con su resultado en `datos`, calculadas por patrones de
    reglas disparadas (rapido) y no por evaluacion completa de cada configuracion."""
    banderas: dict[tuple, list[int]] = {}

    def bandera(config: Configuracion, codigo: str) -> list[int]:
        fila = config.regla(codigo)
        clave = (codigo, fila[3], fila[4])
        if clave not in banderas:
            banderas[clave] = _bandera(config, codigo, datos)
        return banderas[clave]

    # R-07 no tiene perilla en la rejilla: una sola bandera.
    salida: list[tuple[Configuracion, Resultado]] = []
    for config in candidatas():
        mascaras = [
            a | b | c | d | e
            for a, b, c, d, e in zip(
                bandera(config, "R-01"),
                bandera(config, "R-03"),
                bandera(config, "R-05"),
                bandera(config, "R-07"),
                bandera(config, "R-09"),
                strict=True,
            )
        ]
        patrones = Counter(
            (mascara, fraude, patron)
            for mascara, (fraude, patron) in zip(mascaras, datos.etiquetas, strict=True)
        )
        for p7, p9, alerta in itertools.product(PESOS_R07, PESOS_R09, UMBRALES_ALERTA):
            pesos = {
                "R-01": config.regla("R-01")[5],
                "R-03": config.regla("R-03")[5],
                "R-05": config.regla("R-05")[5],
                "R-07": p7,
                "R-09": p9,
            }
            conteos: Counter = Counter()
            for (mascara, fraude, patron), n in patrones.items():
                puntaje = min(
                    sum(pesos[codigo] for codigo, bit in _BITS.items() if mascara & bit), 100
                )
                conteos[(fraude, patron, puntaje >= alerta)] += n
            candidata = config.con("cand", alerta, R_07={"peso": p7}, R_09={"peso": p9})
            salida.append((candidata, resultado_de(conteos)))
    return salida



# ---------------------------------------------------------------------------
# R-11 (T-63): encender la regla encima de la ganadora
# ---------------------------------------------------------------------------

UMBRALES_R11 = (0.5, 0.6, 0.7, 0.8, 0.9)  # proporcion minima reenviada
SALTOS_R11 = (1, 2, 3)
VENTANAS_R11 = (15, 30, 60)
PESOS_R11 = (10, 20, 30, 40)


def _suma_sin_tope(config: Configuracion, datos: Datos) -> list[int]:
    """Por transaccion, lo que suman los pesos de las reglas que disparan (sin el tope)."""
    reglas = reglas_de(config)
    politica = politica_de(config)
    contextos = datos.contextos(*_ventanas(config), _ventana_rasgo(config))
    return [
        sum(d.peso for d in evaluar(txn, contexto, reglas, politica).reglas_disparadas)
        for txn, contexto in zip(datos.transacciones, contextos, strict=True)
    ]


def _bandera_r11(config: Configuracion, datos: Datos) -> list[bool]:
    """Si R-11 dispara en cada transaccion, con el evaluador real y SOLO esa regla.

    Salto honesto, dicho aqui: una transaccion cuyo destino no reenvio nada en el rango
    tiene proporcion 0, y el umbral de la rejilla es mayor que 0, asi que no puede
    disparar. Esas no se pasan por el evaluador (son la inmensa mayoria): se marcan en
    falso. Todas las demas si.
    """
    solo = Configuracion("solo", 0, tuple(f for f in config.reglas if f[0] == "R-11"))
    reglas = reglas_de(solo)
    politica = politica_de(BASE)
    ventana = _ventana_rasgo(config)
    assert ventana is not None
    contextos = datos.contextos(DIAS_HISTORIAL, 30, ventana)
    rasgos = datos.rasgos(ventana)
    return [
        rasgo.monto_reenviado_crc > 0 and bool(evaluar(txn, contexto, reglas, politica).reglas_disparadas)
        for txn, contexto, rasgo in zip(datos.transacciones, contextos, rasgos, strict=True)
    ]


def _con_r11(base: Configuracion, ventana: int, umbral: float, saltos: int, peso: int) -> Configuracion:
    return base.con(
        "con R-11",
        R_11={
            "umbral": umbral,
            "parametros": (("ventana_minutos", ventana), ("saltos_minimos", saltos)),
            "peso": peso,
            "activa": True,
        },
    )


def experimento_r11(calibracion: list[Datos], validacion: list[Datos]) -> int:
    """R-11 activa sobre la ganadora: F1 y exhaustividad antes y despues, por conjunto."""
    base = GANADORA_REJILLA
    todos = [*calibracion, *validacion]
    sumas = {id(d): _suma_sin_tope(base, d) for d in todos}
    antes = {id(d): medir(base, d) for d in todos}
    print(f"\nR-11 sobre la ganadora ({base.nombre}, umbral_alerta {base.umbral_alerta})")
    for datos in todos:
        print(linea(f"sin R-11 / {datos.semilla} {datos.mundo}", antes[id(datos)]))
    piso_base = min(antes[id(d)].exhaustividad for d in validacion)
    f1_base = f1_medio([antes[id(d)] for d in validacion])
    print(f"base: exhaustividad minima en validacion {_pct(piso_base)}, F1 medio {_pct(f1_base)}")

    def resultado(datos: Datos, banderas: list[bool], peso: int) -> Resultado:
        conteos: Counter = Counter()
        for suma, bandera, (fraude, patron) in zip(
            sumas[id(datos)], banderas, datos.etiquetas, strict=True
        ):
            puntaje = min(suma + (peso if bandera else 0), 100)
            conteos[(fraude, patron, puntaje >= base.umbral_alerta)] += 1
        return resultado_de(conteos)

    filas = []
    disparos: dict[tuple, tuple[int, int]] = {}
    for ventana, umbral, saltos in itertools.product(VENTANAS_R11, UMBRALES_R11, SALTOS_R11):
        config = _con_r11(base, ventana, umbral, saltos, PESOS_R11[0])
        banderas = {id(d): _bandera_r11(config, d) for d in todos}
        total = sum(sum(b) for b in banderas.values())
        en_fraude = sum(
            1
            for d in todos
            for bandera, (fraude, _) in zip(banderas[id(d)], d.etiquetas, strict=True)
            if bandera and fraude
        )
        disparos[(ventana, umbral, saltos)] = (total, en_fraude)
        for peso in PESOS_R11:
            r_cal = [resultado(d, banderas[id(d)], peso) for d in calibracion]
            r_val = [resultado(d, banderas[id(d)], peso) for d in validacion]
            filas.append(((ventana, umbral, saltos, peso), r_cal, r_val))

    base_cal = [antes[id(d)] for d in calibracion]
    # Igual que en la rejilla principal: gana el F1 de calibracion y, a igual F1, la que
    # se aparta menos de lo que la semilla ya declara (R-11: 0,8; 15 minutos; 2 saltos;
    # peso 20). Validacion no elige, solo veta.
    semilla_r11 = (15, 0.8, 2, 20)

    def cambios(clave: tuple) -> int:
        return sum(1 for a, b in zip(clave, semilla_r11, strict=True) if a != b)

    filas.sort(key=lambda f: (-round(f1_medio(f[1]), 6), cambios(f[0])))
    print(f"\ncandidatas de R-11: {len(filas)}")
    documentada = next(f for f in filas if f[0] == semilla_r11)
    total, en_fraude = disparos[semilla_r11[:3]]
    print(
        f"la de la semilla (15 min, 0,8, 2 saltos, peso 20): dispara {total} veces ({en_fraude} "
        f"sobre fraude); F1 validacion {_pct(f1_medio(documentada[2]))}, exhaustividad minima "
        f"{_pct(min(r.exhaustividad for r in documentada[2]))}"
    )
    mejores = 0
    ganadora = None
    for (ventana, umbral, saltos, peso), r_cal, r_val in filas:
        total, en_fraude = disparos[(ventana, umbral, saltos)]
        mejora = (
            pasa_piso(r_cal)
            and min(r.exhaustividad for r in r_val) >= piso_base
            and f1_medio(r_val) > f1_base
        )
        if mejora and ganadora is None:
            ganadora = ((ventana, umbral, saltos, peso), r_cal, r_val)
        if mejores < 5 or mejora:
            mejores += 1
            print(
                f"\nventana {ventana}, umbral {umbral}, saltos {saltos}, peso {peso}: R-11 dispara "
                f"{total} veces en los {len(todos)} conjuntos ({en_fraude} sobre fraude); "
                f"{'MEJORA' if mejora else 'no mejora'}"
            )
            print(
                f"   F1 calibracion {_pct(f1_medio(r_cal))} (base {_pct(f1_medio(base_cal))}); "
                f"validacion {_pct(f1_medio(r_val))} (base {_pct(f1_base)}); "
                f"exhaustividad minima validacion {_pct(min(r.exhaustividad for r in r_val))} "
                f"(base {_pct(piso_base)})"
            )
        if ganadora is not None and mejores >= 5:
            break

    if ganadora is None:
        print("\nninguna configuracion de R-11 mejora el F1 de validacion sin bajar el piso: queda inactiva")
        return 1
    (ventana, umbral, saltos, peso), r_cal, r_val = ganadora
    config = _con_r11(base, ventana, umbral, saltos, peso)
    for datos, rapido in zip(todos, [*r_cal, *r_val], strict=True):
        lento = medir(config, datos)
        assert (lento.detectados, lento.falsos_positivos) == (
            rapido.detectados,
            rapido.falsos_positivos,
        )
        print(linea(f"con R-11 / {datos.semilla} {datos.mundo}", lento))
    print(f"\nGANADORA CON R-11 (verificada con el evaluador completo): ventana {ventana}, "
          f"umbral {umbral}, saltos {saltos}, peso {peso}")
    return 0


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------


def _pct(valor: float) -> str:
    return f"{100 * valor:5.1f} %"


def linea(nombre: str, r: Resultado) -> str:
    return (
        f"{nombre:<22} det {r.detectados:>3}/{r.detectados + r.perdidos:<3} "
        f"FP {r.falsos_positivos:>3}  precision {_pct(r.precision)}  "
        f"exhaustividad {_pct(r.exhaustividad)}  F1 {_pct(r.f1)}"
    )


def pasa_piso(resultados: list[Resultado]) -> bool:
    """El piso se exige en CADA semilla de validacion, no solo en el agregado."""
    return all(r.exhaustividad >= PISO_EXHAUSTIVIDAD for r in resultados)


def f1_medio(resultados: list[Resultado]) -> float:
    return sum(r.f1 for r in resultados) / len(resultados)


def principal(argv: list[str] | None = None) -> int:
    analizador = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    analizador.add_argument("--semilla", type=int, help="semilla de calibracion (YAML por omision)")
    analizador.add_argument("--validacion", type=int, nargs="+", default=list(SEMILLAS_VALIDACION))
    analizador.add_argument("--top", type=int, default=5, help="rechazadas que se muestran")
    analizador.add_argument(
        "--r11",
        action="store_true",
        help="solo el experimento de R-11 sobre la ganadora (T-63), sin la rejilla principal",
    )
    argumentos = analizador.parse_args(argv)

    semilla = argumentos.semilla or obtener_ajustes().generador.semilla
    # Calibracion: la semilla del YAML en los dos mundos (el de las pruebas, 4 entidades,
    # y el del YAML, 5). Validacion: otras semillas, tambien en los dos mundos. La
    # busqueda nunca ve la validacion.
    calibracion = [generar_datos(semilla), generar_datos(semilla, entidades_del_yaml=True)]
    validacion = [
        generar_datos(s, entidades_del_yaml=yaml)
        for s in argumentos.validacion
        for yaml in (False, True)
    ]

    if argumentos.r11:
        return experimento_r11(calibracion, validacion)

    base_cal = [medir(BASE, d) for d in calibracion]
    base_val = [medir(BASE, d) for d in validacion]
    print(
        f"calibracion: semilla {semilla} (mundo de las pruebas y del YAML); validacion: "
        f"semillas {argumentos.validacion} en los dos mundos"
    )
    for datos, r in zip(calibracion, base_cal, strict=True):
        print(linea(f"base / cal {datos.semilla} {datos.mundo}", r))
    for datos, r in zip(validacion, base_val, strict=True):
        print(linea(f"base / val {datos.semilla} {datos.mundo}", r))

    # `buscar` devuelve las mismas candidatas, en el mismo orden, para cada conjunto:
    # asi se alinean por posicion. Es la via rapida (patrones de reglas disparadas); la
    # ganadora se re-mide despues con el evaluador completo y tiene que coincidir.
    en_calibracion = [buscar(d) for d in calibracion]
    en_validacion = [buscar(d) for d in validacion]
    filas = []
    for posicion, (config, _) in enumerate(en_calibracion[0]):
        r_cal = [lista[posicion][1] for lista in en_calibracion]
        if not pasa_piso(r_cal):
            continue
        r_val = [lista[posicion][1] for lista in en_validacion]
        filas.append((config, r_cal, r_val))
    # El orden usa SOLO calibracion: F1 medio desc y, a igual F1, menos cambios.
    # Validacion no elige; solo veta (piso por conjunto y mejora del F1 medio).
    filas.sort(key=lambda f: (-round(f1_medio(f[1]), 6), len(f[0].diferencias(BASE))))
    print(
        f"\ncandidatas: {len(en_calibracion[0])}; con exhaustividad de calibracion >= "
        f"{_pct(PISO_EXHAUSTIVIDAD)} en cada conjunto: {len(filas)}"
    )

    ganadora = None
    for indice, (config, r_cal, r_val) in enumerate(filas):
        valido = pasa_piso(r_val) and f1_medio(r_val) > f1_medio(base_val)
        if indice < argumentos.top or valido:
            print(f"\n#{indice + 1} {'GANA' if valido else 'rechazada en validacion'}")
            for cambio in config.diferencias(BASE):
                print(f"   {cambio}")
            for datos, r in zip(calibracion, r_cal, strict=True):
                print("   " + linea(f"cal {datos.semilla} {datos.mundo}", r))
            for datos, r in zip(validacion, r_val, strict=True):
                print("   " + linea(f"val {datos.semilla} {datos.mundo}", r))
            print(
                f"   F1 medio calibracion {_pct(f1_medio(r_cal))} (base {_pct(f1_medio(base_cal))})"
                f"; validacion {_pct(f1_medio(r_val))} (base {_pct(f1_medio(base_val))})"
            )
        if valido:
            ganadora = (config, r_cal, r_val)
            break

    if ganadora is None:
        print("\nninguna candidata mejora en validacion: se deja la configuracion base")
        return 1
    config, r_cal, r_val = ganadora
    # La verificacion final: la ganadora con el evaluador completo, no la via rapida.
    for datos, rapido in zip([*calibracion, *validacion], [*r_cal, *r_val], strict=True):
        lento = medir(config, datos)
        assert (lento.detectados, lento.falsos_positivos) == (
            rapido.detectados,
            rapido.falsos_positivos,
        )
    print("\nGANADORA (verificada con el evaluador completo)")
    for cambio in config.diferencias(BASE):
        print(f"  {cambio}")
    for patron, fraudes, detectados in medir(config, calibracion[0]).por_patron:
        print(f"    {patron:<30} {detectados}/{fraudes}  (calibracion, mundo de las pruebas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
