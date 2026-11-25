"""H-08 · Evaluador de reglas y puntaje.

`evaluar(transaccion, contexto, reglas, politica) -> Evaluacion` es una funcion pura:
no hace I/O, no lee el reloj (la hora sale de `transaccion.fecha`) y no conoce la base.
Todo lo que necesita de Mongo lo precalcula H-09 y lo entrega en `ContextoEvaluacion`.
Por eso se prueba sin base de datos (`pruebas/unitarias/prueba_evaluador.py`).

Lo que sale de aqui:

- `puntaje`: suma de los pesos de las reglas que dispararon, topada en
  `politica.puntaje_maximo`.
- `severidad`: el tramo de `politica.cortes_severidad` que contiene el puntaje, o
  `None` si no cae en ninguno (por debajo de `umbral_alerta` no hay alerta).
- `reglas_disparadas`: codigo, regla_id, version, peso aportado y una explicacion en
  espanol con el valor observado contra el umbral. La version va porque la alerta
  tiene que poder explicarse aunque la regla cambie despues (H-16).
- `reglas_no_evaluadas`: las que no se pudieron evaluar, cada una con su motivo. Un
  `tipo` desconocido o un parametro ausente no revientan el motor: se reportan.

Los numeros de negocio (umbrales, pesos, cortes) llegan como datos. Ninguno esta
escrito aqui.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field

from app.modelos.base import ZONA_COSTA_RICA, CuentaSinpe, MontoCRC
from app.modelos.estados import Severidad
from app.modelos.identidad import PerfilComportamiento
from app.modelos.transaccional import EntradaListaRiesgo, Regla, Transaccion

MINUTOS_DIA = 24 * 60


class _Congelado(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------


class CorteSeveridad(_Congelado):
    """Un tramo de `politica_deteccion.cortes_severidad`. Limites inclusivos."""

    severidad: Severidad
    puntaje_minimo: int = Field(ge=0)
    puntaje_maximo: int = Field(ge=0)


class PoliticaDeteccion(_Congelado):
    """Documento vigente de `politica_deteccion`, con la forma de
    `scripts/semilla/03-catalogos.js`. Solo los campos que el evaluador usa; el resto
    (notificacion, vigente_desde) se ignora aqui y lo lee quien lo necesite (D2).
    """

    politica_id: str = Field(alias="_id")
    version: int = Field(ge=1)
    umbral_alerta: int = Field(ge=0)
    puntaje_maximo: int = Field(ge=1)
    cortes_severidad: tuple[CorteSeveridad, ...] = Field(min_length=1)


class VentanaOrigen(_Congelado):
    """Transferencias del mismo origen dentro de la ventana de R-09.

    Incluye la transaccion que se esta evaluando: una rafaga de 3 es esta y dos mas.
    `ventana_minutos` es la ventana con la que H-09 conto; si no coincide con la de la
    regla, la regla no se evalua (el supervisor cambio la ventana y el contexto quedo
    viejo).
    """

    ventana_minutos: int = Field(ge=1)
    cantidad: int = Field(ge=1)
    monto_acumulado_crc: MontoCRC


class RasgoRed(_Congelado):
    """Rasgos de grafo de UNA cuenta destino (T-63), materializados por D3.

    Es lo que R-11 lee: cuanto recibio la cuenta, cuanto de eso reenvio dentro de
    `ventana_minutos` y en cuantos saltos sigue el dinero. El calculo no es de este
    modulo (lo hace el pipeline de `app/repos/rasgos_grafo.py`); aqui llega ya hecho.

    El rasgo cubre las transferencias de `[desde, corte)`. Una cuenta sin movimientos en
    ese rango llega con todo en cero: es un rasgo valido que dice "no paso nada", no un
    rasgo ausente. `vigencia_segundos` es cuanto despues de `corte` se sigue dando por
    bueno (D3 lo recalcula cada pocos segundos; si D3 se detiene, el rasgo envejece).
    """

    cuenta: CuentaSinpe
    ventana_minutos: int = Field(ge=1)
    desde: datetime
    corte: datetime
    vigencia_segundos: int = Field(ge=1)
    fan_in: int = Field(ge=0, description="Origenes distintos que le enviaron")
    fan_out: int = Field(ge=0, description="Destinos distintos a los que envio")
    monto_recibido_crc: MontoCRC
    monto_reenviado_crc: MontoCRC
    proporcion_pb: int = Field(
        ge=0, le=10_000, description="Reenviado sobre recibido, en puntos basicos (10.000 = 100 %)"
    )
    minutos_reenvio: int = Field(ge=0, description="Minutos hasta el ultimo reenvio contado")
    saltos: int = Field(ge=0, description="Largo de la cadena de reenvios que parte de la cuenta")


class ContextoEvaluacion(_Congelado):
    """Hechos que la consulta de H-09 precalcula antes de llamar a `evaluar`.

    Todos los campos son obligatorios a proposito: un contexto incompleto no puede
    pasar por "sin historial". `perfil` si admite `None` explicito (cliente sin perfil
    materializado todavia); en ese caso R-01 y R-05 quedan como no evaluadas.
    """

    perfil: PerfilComportamiento | None
    # Transferencias previas del cliente por destino dentro de `dias_historial` (R-03),
    # sin contar la transaccion evaluada. Un destino ausente cuenta como 0.
    dias_historial: int = Field(ge=1)
    destinos_conocidos: Mapping[CuentaSinpe, int]
    # Entradas de `listas_riesgo` para la cuenta destino, sin filtrar por fecha: el
    # filtro "vigente al momento de la transaccion" lo aplica el evaluador (R-07).
    entradas_lista_riesgo: tuple[EntradaListaRiesgo, ...]
    ventana_origen: VentanaOrigen
    # Rasgos de grafo del destino (R-11). Es la unica excepcion a "todos obligatorios":
    # `None` significa que D3 todavia no los materializo, y R-11 lo reporta como no
    # evaluada; no se confunde con "sin movimientos", que es un `RasgoRed` en ceros.
    rasgo_red: RasgoRed | None = None


# ---------------------------------------------------------------------------
# Salidas
# ---------------------------------------------------------------------------


class ReglaDisparada(_Congelado):
    codigo: str
    regla_id: str
    version: int
    peso: int = Field(ge=0, description="Peso aportado al puntaje, antes del tope")
    explicacion: str


class ReglaNoEvaluada(_Congelado):
    codigo: str
    regla_id: str
    version: int | None
    motivo: str


class Evaluacion(_Congelado):
    transaccion_id: str
    puntaje: int = Field(ge=0)
    severidad: Severidad | None
    genera_alerta: bool
    politica_id: str
    politica_version: int
    reglas_disparadas: tuple[ReglaDisparada, ...]
    reglas_no_evaluadas: tuple[ReglaNoEvaluada, ...]

    @property
    def regla_ids(self) -> list[str]:
        """Forma v1 de `alertas.reglas_disparadas` (lista de REG-xxx).

        D1 escribe v2 con la copia embebida; esto queda para leer alertas v1 y
        para quien solo necesite los identificadores."""
        return [disparada.regla_id for disparada in self.reglas_disparadas]


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Resultado:
    dispara: bool
    explicacion: str = ""
    no_evaluada: str | None = None


def _no_evaluada(motivo: str) -> _Resultado:
    return _Resultado(dispara=False, no_evaluada=motivo)


def _exacto(valor: float) -> Fraction:
    """Numero de la regla como fraccion exacta: 0.8 es 4/5, no 0.8000000000000000444."""
    return Fraction(str(valor))


def _colones(monto: int) -> str:
    return f"{monto:,}".replace(",", ".") + " CRC"


def _decimal(valor: Fraction) -> str:
    """Un decimal, coma decimal: 16,6. Los enteros van sin decimal: 10."""
    if valor.denominator == 1:
        return str(valor.numerator)
    decimas = round(valor * 10)
    return f"{decimas // 10},{decimas % 10}"


def _parametro(regla: Regla, nombre: str) -> float | None:
    return regla.parametros.get(nombre)


def _regla_monto(
    regla: Regla, transaccion: Transaccion, contexto: ContextoEvaluacion
) -> _Resultado:
    if regla.umbral is None:
        return _no_evaluada("la regla no tiene umbral")
    if contexto.perfil is None:
        return _no_evaluada("el cliente no tiene perfil de comportamiento")
    promedio = contexto.perfil.promedio_crc
    if promedio <= 0:
        return _no_evaluada("el perfil no tiene promedio (cliente sin historial)")
    umbral = _exacto(regla.umbral)
    monto = transaccion.monto_crc
    if not monto > umbral * promedio:
        return _Resultado(dispara=False)
    return _Resultado(
        dispara=True,
        explicacion=(
            f"monto {_colones(monto)} es {_decimal(Fraction(monto, promedio))} veces el "
            f"promedio del cliente ({_colones(promedio)}); umbral {_decimal(umbral)} veces"
        ),
    )


def _regla_destino(
    regla: Regla, transaccion: Transaccion, contexto: ContextoEvaluacion
) -> _Resultado:
    if regla.umbral is None:
        return _no_evaluada("la regla no tiene umbral")
    dias = _parametro(regla, "dias_historial")
    if dias is None:
        return _no_evaluada("falta el parametro dias_historial")
    if dias != contexto.dias_historial:
        return _no_evaluada(
            f"el contexto se calculo con {contexto.dias_historial} dias y la regla pide {dias}"
        )
    previas = contexto.destinos_conocidos.get(transaccion.cuenta_destino, 0)
    if not previas <= _exacto(regla.umbral):
        return _Resultado(dispara=False)
    return _Resultado(
        dispara=True,
        explicacion=(
            f"destino {transaccion.cuenta_destino} con {previas} transferencias previas en "
            f"{dias} dias; es nuevo con hasta {_decimal(_exacto(regla.umbral))}"
        ),
    )


def _minutos(hhmm: str) -> int:
    horas, minutos = hhmm.split(":")
    return int(horas) * 60 + int(minutos)


def _regla_horario(
    regla: Regla, transaccion: Transaccion, contexto: ContextoEvaluacion
) -> _Resultado:
    if regla.umbral is None:
        return _no_evaluada("la regla no tiene umbral")
    if contexto.perfil is None:
        return _no_evaluada("el cliente no tiene perfil de comportamiento")
    horario = contexto.perfil.horario_habitual
    inicio, fin = _minutos(horario.inicio), _minutos(horario.fin)
    # El horario habitual esta en hora de Costa Rica; la transaccion, en UTC.
    local = transaccion.fecha.astimezone(ZONA_COSTA_RICA)
    minuto = local.hour * 60 + local.minute
    # Una ventana con inicio > fin cruza la medianoche (22:00-06:00).
    dentro = (
        inicio <= minuto <= fin
        if inicio <= fin
        else (minuto >= inicio or minuto <= fin)
    )
    if dentro:
        return _Resultado(dispara=False)
    fuera_por = min((inicio - minuto) % MINUTOS_DIA, (minuto - fin) % MINUTOS_DIA)
    holgura = _exacto(regla.umbral)
    if not fuera_por > holgura:
        return _Resultado(dispara=False)
    return _Resultado(
        dispara=True,
        explicacion=(
            f"{local:%H:%M} hora de Costa Rica, {fuera_por} minutos fuera del horario "
            f"habitual {horario.inicio}-{horario.fin}; holgura {_decimal(holgura)} minutos"
        ),
    )


def _regla_lista_riesgo(
    regla: Regla, transaccion: Transaccion, contexto: ContextoEvaluacion
) -> _Resultado:
    if regla.umbral is None:
        return _no_evaluada("la regla no tiene umbral")
    # Vigente AL MOMENTO de la transaccion: una cuenta reportada despues no cuenta.
    # Si se compara contra la lista de hoy, TXN-001 da 115 y no 75.
    coincidencias = sum(
        1
        for entrada in contexto.entradas_lista_riesgo
        if entrada.cuenta == transaccion.cuenta_destino
        and entrada.vigente
        and entrada.fecha_reporte <= transaccion.fecha
    )
    if not coincidencias >= _exacto(regla.umbral):
        return _Resultado(dispara=False)
    return _Resultado(
        dispara=True,
        explicacion=(
            f"destino {transaccion.cuenta_destino} con {coincidencias} entradas vigentes en "
            f"lista de riesgo a la fecha de la transaccion; umbral "
            f"{_decimal(_exacto(regla.umbral))}"
        ),
    )


def _regla_velocidad(
    regla: Regla, transaccion: Transaccion, contexto: ContextoEvaluacion
) -> _Resultado:
    if regla.umbral is None:
        return _no_evaluada("la regla no tiene umbral")
    ventana = _parametro(regla, "ventana_minutos")
    piso = _parametro(regla, "monto_acumulado_crc")
    if ventana is None or piso is None:
        return _no_evaluada(
            "faltan los parametros ventana_minutos o monto_acumulado_crc"
        )
    origen = contexto.ventana_origen
    if ventana != origen.ventana_minutos:
        return _no_evaluada(
            f"el contexto se calculo con {origen.ventana_minutos} minutos y la regla pide {ventana}"
        )
    if not (
        origen.cantidad >= _exacto(regla.umbral) and origen.monto_acumulado_crc >= piso
    ):
        return _Resultado(dispara=False)
    return _Resultado(
        dispara=True,
        explicacion=(
            f"{origen.cantidad} transferencias del mismo origen en {ventana} minutos por "
            f"{_colones(origen.monto_acumulado_crc)}; umbral "
            f"{_decimal(_exacto(regla.umbral))} transferencias y {_colones(int(piso))}"
        ),
    )


def _hora_cr(fecha: datetime) -> str:
    return f"{fecha.astimezone(ZONA_COSTA_RICA):%H:%M}"


def _regla_red_mulas(
    regla: Regla, transaccion: Transaccion, contexto: ContextoEvaluacion
) -> _Resultado:
    """R-11: el destino reenvia casi todo lo recibido, en pocos minutos y en cadena.

    Lee el rasgo materializado del destino; no busca nada. Sin rasgo, con rasgo de otra
    ventana, que no cubre la fecha de la transaccion o que ya envejecio, queda como no
    evaluada con su motivo: no dispara a ciegas.
    """
    if regla.umbral is None:
        return _no_evaluada("la regla no tiene umbral")
    ventana = _parametro(regla, "ventana_minutos")
    saltos_minimos = _parametro(regla, "saltos_minimos")
    if ventana is None or saltos_minimos is None:
        return _no_evaluada("faltan los parametros ventana_minutos o saltos_minimos")
    rasgo = contexto.rasgo_red
    if rasgo is None:
        return _no_evaluada(
            "no hay rasgos de grafo calculados; D3 los materializa cuando R-11 esta activa"
        )
    if rasgo.cuenta != transaccion.cuenta_destino:
        return _no_evaluada(
            f"el rasgo es de {rasgo.cuenta} y el destino es {transaccion.cuenta_destino}"
        )
    if ventana != rasgo.ventana_minutos:
        return _no_evaluada(
            f"el rasgo se calculo con {rasgo.ventana_minutos} minutos y la regla pide {ventana}"
        )
    if transaccion.fecha < rasgo.desde:
        return _no_evaluada(
            f"el rasgo cubre desde las {_hora_cr(rasgo.desde)} y la transaccion es anterior"
        )
    vence = rasgo.corte + timedelta(seconds=rasgo.vigencia_segundos)
    if transaccion.fecha > vence:
        return _no_evaluada(
            f"el rasgo es viejo: se calculo a las {_hora_cr(rasgo.corte)} y vale "
            f"{rasgo.vigencia_segundos} segundos"
        )
    if rasgo.monto_recibido_crc <= 0:
        return _Resultado(dispara=False)
    umbral = _exacto(regla.umbral)
    # Exacto: reenviado / recibido contra el umbral, sin pasar por un flotante.
    proporcion = Fraction(rasgo.monto_reenviado_crc, rasgo.monto_recibido_crc)
    if not (proporcion >= umbral and rasgo.saltos >= _exacto(saltos_minimos)):
        return _Resultado(dispara=False)
    return _Resultado(
        dispara=True,
        explicacion=(
            f"destino {rasgo.cuenta} recibio {_colones(rasgo.monto_recibido_crc)} y reenvio "
            f"{_colones(rasgo.monto_reenviado_crc)} ({_decimal(Fraction(rasgo.proporcion_pb, 100))} %) "
            f"en {rasgo.minutos_reenvio} minutos, en una cadena de {rasgo.saltos} saltos; "
            f"umbral {_decimal(umbral * 100)} % en {_decimal(_exacto(ventana))} minutos y "
            f"{_decimal(_exacto(saltos_minimos))} saltos"
        ),
    )


_Evaluador = Callable[[Regla, Transaccion, ContextoEvaluacion], _Resultado]

EVALUADORES: Mapping[str, _Evaluador] = {
    "monto": _regla_monto,
    "destino": _regla_destino,
    "horario": _regla_horario,
    "lista_riesgo": _regla_lista_riesgo,
    "velocidad": _regla_velocidad,
    "red_mulas": _regla_red_mulas,
}


@dataclass(frozen=True)
class TipoRegla:
    """Lo que lee cada tipo de regla: que significa su `umbral` y que `parametros` pide.

    Es una declaracion, no logica: el evaluador no la consulta. La usa la gestion de
    reglas (H-16) para rechazar una regla que el motor no sabria evaluar, y
    `pruebas/contrato/prueba_reglas_api.py` comprueba que coincide con lo que cada
    funcion de arriba lee de verdad.

    `evaluable` es falso cuando el motor todavia no evalua el tipo en linea; sus
    parametros se declaran igual para que las reglas del catalogo se puedan editar.
    Hoy todos los tipos son evaluables: `red_mulas` lo es desde T-63 (rasgos de grafo).
    """

    umbral: str
    parametros: Mapping[str, str]
    evaluable: bool = True


TIPOS_REGLA: Mapping[str, TipoRegla] = {
    "monto": TipoRegla(umbral="veces el promedio_crc del perfil del cliente", parametros={}),
    "destino": TipoRegla(
        umbral="maximo de transferencias previas al destino para que cuente como nuevo",
        parametros={"dias_historial": "dias de historial que se miran"},
    ),
    "horario": TipoRegla(
        umbral="minutos de holgura fuera del horario habitual del cliente", parametros={}
    ),
    "lista_riesgo": TipoRegla(
        umbral="coincidencias vigentes minimas en listas_riesgo", parametros={}
    ),
    "velocidad": TipoRegla(
        umbral="transferencias minimas del mismo origen dentro de la ventana",
        parametros={
            "ventana_minutos": "ventana en la que se cuentan las transferencias",
            "monto_acumulado_crc": "piso de colones acumulados en la ventana",
        },
    ),
    "red_mulas": TipoRegla(
        umbral="proporcion minima de lo recibido que el destino reenvia",
        parametros={
            "ventana_minutos": "minutos en los que tiene que ocurrir el reenvio",
            "saltos_minimos": "saltos minimos para llamarlo cadena",
        },
    ),
}

assert set(TIPOS_REGLA) == set(EVALUADORES), (
    "TIPOS_REGLA y EVALUADORES tienen que declarar los mismos tipos"
)


# ---------------------------------------------------------------------------
# Puntaje y severidad
# ---------------------------------------------------------------------------


def severidad_de(puntaje: int, politica: PoliticaDeteccion) -> Severidad | None:
    for corte in politica.cortes_severidad:
        if corte.puntaje_minimo <= puntaje <= corte.puntaje_maximo:
            return corte.severidad
    return None


def evaluar(
    transaccion: Transaccion,
    contexto: ContextoEvaluacion,
    reglas: Sequence[Regla],
    politica: PoliticaDeteccion,
) -> Evaluacion:
    """Evalua `transaccion` contra las reglas activas y aplica la politica."""
    disparadas: list[ReglaDisparada] = []
    no_evaluadas: list[ReglaNoEvaluada] = []

    for regla in reglas:
        if not regla.activa:
            continue
        evaluador = EVALUADORES.get(regla.tipo)
        version = regla.version
        if evaluador is None:
            resultado = _no_evaluada(f"tipo de regla desconocido: {regla.tipo}")
        elif version is None:
            resultado = _no_evaluada(
                "la regla no tiene version; la alerta la tiene que registrar"
            )
        else:
            resultado = evaluador(regla, transaccion, contexto)

        if resultado.no_evaluada is not None or version is None:
            no_evaluadas.append(
                ReglaNoEvaluada(
                    codigo=regla.codigo,
                    regla_id=regla.regla_id,
                    version=version,
                    motivo=resultado.no_evaluada or "",
                )
            )
        elif resultado.dispara:
            disparadas.append(
                ReglaDisparada(
                    codigo=regla.codigo,
                    regla_id=regla.regla_id,
                    version=version,
                    peso=regla.peso,
                    explicacion=resultado.explicacion,
                )
            )

    puntaje = min(sum(d.peso for d in disparadas), politica.puntaje_maximo)
    return Evaluacion(
        transaccion_id=transaccion.transaccion_id,
        puntaje=puntaje,
        severidad=severidad_de(puntaje, politica),
        genera_alerta=puntaje >= politica.umbral_alerta,
        politica_id=politica.politica_id,
        politica_version=politica.version,
        reglas_disparadas=tuple(disparadas),
        reglas_no_evaluadas=tuple(no_evaluadas),
    )
