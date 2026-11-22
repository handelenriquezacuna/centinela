"""H-03 · Poblacion: clientes, cuentas y perfiles de comportamiento.

Cada cliente nace con habitos propios: un rango de montos, un horario habitual y unos
destinos frecuentes. La cuenta lleva la entidad financiera, y la entidad fija el tope
de SINPE Movil: ninguna transaccion posterior puede superarlo.

Los perfiles se calculan DESPUES de generar las transacciones legitimas, a partir de lo
que de verdad se genero (promedio, maximo, cantidad), para que lo que lee el evaluador
(`ContextoEvaluacion.perfil`) sea coherente con el historial y no una promesa.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.modelos.base import ESQUEMA_VERSION
from app.nucleo.config import AjustesGenerador, EntidadSinpe

# Numeros que ya usan los datos de la Practica 1 y el demo: el generador no los repite
# (el indice de cuentas.cuenta_sinpe y el de clientes.cedula son unicos).
CUENTAS_RESERVADAS = frozenset(
    {
        "8712-4455", "6210-3387", "5544-9021", "3390-6612",
        "2233-4455", "3344-5566", "4455-6677",
        "6033-9001", "7104-2288", "8455-1177", "6001-3344",
    }
)  # fmt: skip
CEDULAS_RESERVADAS = frozenset({"1-1111-1111", "2-2222-2222", "3-3333-3333", "4-4444-4444"})

NOMBRES = (
    "Maria", "Jose", "Luis", "Ana", "Carlos", "Laura", "Andres", "Daniela", "Diego",
    "Valeria", "Jorge", "Sofia", "Mario", "Paola", "Esteban", "Natalia", "Ricardo",
    "Karla", "Fabian", "Gabriela", "Alonso", "Mariana", "Kevin", "Priscilla", "Oscar",
    "Raquel", "Luis Diego", "Maria Fernanda", "Jose Pablo", "Ana Lucia",
)  # fmt: skip
APELLIDOS = (
    "Rodriguez", "Fernandez", "Castro", "Mora", "Jimenez", "Vargas", "Solano", "Rojas",
    "Gonzalez", "Calderon", "Araya", "Chaves", "Quesada", "Salazar", "Camacho",
    "Hernandez", "Soto", "Alvarado", "Brenes", "Cordero", "Villalobos", "Umana",
    "Zuniga", "Barrantes", "Segura", "Madrigal", "Porras", "Montero", "Arce", "Cespedes",
)  # fmt: skip

# Horarios habituales (hora local de Costa Rica): (inicio, fin) en horas, con su peso.
# El fin es exclusivo en horas pero el perfil lo declara como HH:00 inclusivo, igual que
# lo lee el evaluador. El nocturno cruza la medianoche.
HORARIOS = (
    ((7, 19), 0.45),
    ((6, 18), 0.20),
    ((9, 21), 0.15),
    ((6, 22), 0.12),
    ((18, 2), 0.08),
)

# Un cliente con promedio objetivo p manda a lo sumo MULTIPLO_MAXIMO * p: lejos del
# umbral de 10 veces de R-01, para que lo legitimo no dispare esa regla.
MULTIPLO_MAXIMO = 5
# El promedio objetivo no pasa de tope / DIVISOR_PROMEDIO: asi un monto atipico (11 a 13
# veces el promedio) cabe bajo el tope de la entidad.
DIVISOR_PROMEDIO = 14
PROMEDIO_MINIMO_CRC = 3_000
MONTO_MINIMO_CRC = 1_000
REDONDEO_CRC = 500


def redondear_monto(monto: float) -> int:
    """Colones enteros, en multiplos de 500 (SINPE Movil no opera centimos)."""
    return round(monto / REDONDEO_CRC) * REDONDEO_CRC


def hhmm(hora: int) -> str:
    return f"{hora % 24:02d}:00"


@dataclass(frozen=True, slots=True)
class ClienteGen:
    cliente_id: str
    nombre: str
    cedula: str
    fecha_registro: datetime


@dataclass(frozen=True, slots=True)
class CuentaGen:
    cuenta_id: str
    cuenta_sinpe: str
    cliente_id: str
    entidad: str
    tope_crc: int


@dataclass(frozen=True, slots=True)
class HabitosGen:
    """Lo que el cliente HARA (objetivo); el perfil sale de lo que de verdad hizo."""

    cliente_id: str
    promedio_objetivo_crc: int
    maximo_objetivo_crc: int
    hora_inicio: int
    hora_fin: int
    destinos_frecuentes: tuple[str, ...]
    actividad: float

    def horas_habituales(self) -> tuple[int, ...]:
        """Horas locales (0-23) dentro del horario habitual."""
        if self.hora_inicio <= self.hora_fin:
            return tuple(range(self.hora_inicio, self.hora_fin))
        return tuple(range(self.hora_inicio, 24)) + tuple(range(self.hora_fin))


@dataclass(frozen=True, slots=True)
class PerfilGen:
    cliente_id: str
    promedio_crc: int
    maximo_crc: int
    desviacion_crc: int
    num_transferencias: int
    hora_inicio: int
    hora_fin: int
    destinos_frecuentes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Poblacion:
    clientes: tuple[ClienteGen, ...]
    cuentas: tuple[CuentaGen, ...]
    habitos: tuple[HabitosGen, ...]
    # Cuentas externas (sin documento en `cuentas`): destinos ocasionales y mulas.
    destinos_externos: tuple[str, ...]
    mulas_externas: tuple[str, ...]
    # Cuentas del banco que actuan de mula en una cadena (tienen su propio documento).
    cuentas_mula: tuple[str, ...]


def _numero_sinpe(rng: random.Random, usados: set[str]) -> str:
    while True:
        numero = f"{rng.randint(2000, 9999)}-{rng.randint(0, 9999):04d}"
        if numero not in usados and numero not in CUENTAS_RESERVADAS:
            usados.add(numero)
            return numero


def _cedula(rng: random.Random, usadas: set[str]) -> str:
    while True:
        cedula = f"{rng.randint(1, 9)}-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"
        if cedula not in usadas and cedula not in CEDULAS_RESERVADAS:
            usadas.add(cedula)
            return cedula


def _promedio_objetivo(rng: random.Random, tope_crc: int) -> int:
    """Log-uniforme entre el piso y tope/14: hay clientes de montos chicos y grandes."""
    techo = max(PROMEDIO_MINIMO_CRC, tope_crc // DIVISOR_PROMEDIO)
    valor = math.exp(rng.uniform(math.log(PROMEDIO_MINIMO_CRC), math.log(techo)))
    return max(PROMEDIO_MINIMO_CRC, redondear_monto(valor))


def generar_poblacion(
    ajustes: AjustesGenerador, rng: random.Random, *, clientes: int | None = None
) -> Poblacion:
    """N clientes, una cuenta cada uno, con entidad, habitos y destinos propios."""
    cantidad = clientes if clientes is not None else ajustes.clientes
    entidades: Sequence[EntidadSinpe] = ajustes.entidades
    inicio_historia = datetime(
        ajustes.fecha_fin.year, ajustes.fecha_fin.month, ajustes.fecha_fin.day, tzinfo=UTC
    ) - timedelta(days=ajustes.dias_historia)

    numeros: set[str] = set()
    cedulas: set[str] = set()

    # Pools externos primero: los destinos de la poblacion salen de aqui, y las mulas
    # son un pool aparte, asi que un destino de fraude nunca es un destino habitual.
    externos = tuple(_numero_sinpe(rng, numeros) for _ in range(max(40, cantidad * 2)))
    mulas_externas = tuple(_numero_sinpe(rng, numeros) for _ in range(30))

    horarios = [horario for horario, _ in HORARIOS]
    pesos_horario = [peso for _, peso in HORARIOS]

    lista_clientes: list[ClienteGen] = []
    lista_cuentas: list[CuentaGen] = []
    lista_habitos: list[HabitosGen] = []

    for indice in range(1, cantidad + 1):
        cliente_id = f"CLI-{indice:04d}"
        cuenta_id = f"CTA-{indice:04d}"
        entidad = rng.choice(entidades)
        nombre = f"{rng.choice(NOMBRES)} {rng.choice(APELLIDOS)} {rng.choice(APELLIDOS)}"
        registro = inicio_historia - timedelta(days=rng.randint(30, 900))

        lista_clientes.append(
            ClienteGen(cliente_id, nombre, _cedula(rng, cedulas), registro)
        )
        lista_cuentas.append(
            CuentaGen(
                cuenta_id,
                _numero_sinpe(rng, numeros),
                cliente_id,
                entidad.nombre,
                entidad.tope_crc,
            )
        )

        promedio = _promedio_objetivo(rng, entidad.tope_crc)
        hora_inicio, hora_fin = rng.choices(horarios, weights=pesos_horario, k=1)[0]
        cantidad_destinos = rng.randint(3, 6)
        destinos = tuple(rng.sample(externos, cantidad_destinos))
        lista_habitos.append(
            HabitosGen(
                cliente_id=cliente_id,
                promedio_objetivo_crc=promedio,
                maximo_objetivo_crc=min(entidad.tope_crc, promedio * MULTIPLO_MAXIMO),
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
                destinos_frecuentes=destinos,
                actividad=rng.uniform(0.3, 2.5),
            )
        )

    cantidad_mulas = max(4, round(cantidad * 0.03))
    cuentas_mula = tuple(
        cuenta.cuenta_id for cuenta in rng.sample(lista_cuentas, min(cantidad_mulas, cantidad))
    )

    return Poblacion(
        clientes=tuple(lista_clientes),
        cuentas=tuple(lista_cuentas),
        habitos=tuple(lista_habitos),
        destinos_externos=externos,
        mulas_externas=mulas_externas,
        cuentas_mula=cuentas_mula,
    )


def construir_perfiles(
    poblacion: Poblacion, legitimas: Iterable[tuple[str, str, int]]
) -> list[PerfilGen]:
    """Perfil de cada cliente a partir de sus transacciones LEGITIMAS reales.

    `legitimas` son tuplas (cuenta_origen, cuenta_destino, monto_crc). El fraude no entra:
    un perfil que ya incluyera el fraude lo volveria "normal" para el motor.
    """
    por_cuenta: dict[str, list[int]] = {}
    destinos: dict[str, Counter[str]] = {}
    for origen, destino, monto in legitimas:
        por_cuenta.setdefault(origen, []).append(monto)
        destinos.setdefault(origen, Counter())[destino] += 1

    cuenta_a_cliente = {cuenta.cuenta_id: cuenta.cliente_id for cuenta in poblacion.cuentas}
    habitos = {h.cliente_id: h for h in poblacion.habitos}

    perfiles: list[PerfilGen] = []
    for cuenta_id, montos in por_cuenta.items():
        cliente_id = cuenta_a_cliente[cuenta_id]
        habito = habitos[cliente_id]
        frecuentes = [d for d, veces in destinos[cuenta_id].most_common() if veces >= 2]
        if not frecuentes:
            frecuentes = [destinos[cuenta_id].most_common(1)[0][0]]
        perfiles.append(
            PerfilGen(
                cliente_id=cliente_id,
                promedio_crc=max(1, round(statistics.fmean(montos))),
                maximo_crc=max(montos),
                desviacion_crc=round(statistics.pstdev(montos)) if len(montos) > 1 else 0,
                num_transferencias=len(montos),
                hora_inicio=habito.hora_inicio,
                hora_fin=habito.hora_fin,
                destinos_frecuentes=tuple(frecuentes),
            )
        )
    perfiles.sort(key=lambda perfil: perfil.cliente_id)
    return perfiles


# ---------------------------------------------------------------------------
# A documentos (lo que escribe el repositorio)
# ---------------------------------------------------------------------------


def documento_cliente(cliente: ClienteGen) -> dict:
    return {
        "_id": cliente.cliente_id,
        "nombre": cliente.nombre,
        "cedula": cliente.cedula,
        "fecha_registro": cliente.fecha_registro,
        "esquema_version": ESQUEMA_VERSION,
    }


def documento_cuenta(cuenta: CuentaGen) -> dict:
    return {
        "_id": cuenta.cuenta_id,
        "cuenta_sinpe": cuenta.cuenta_sinpe,
        "cliente_id": cuenta.cliente_id,
        "entidad": cuenta.entidad,
        "esquema_version": ESQUEMA_VERSION,
    }


def documento_perfil(perfil: PerfilGen) -> dict:
    return {
        "_id": perfil.cliente_id,
        "promedio_crc": perfil.promedio_crc,
        "maximo_crc": perfil.maximo_crc,
        "desviacion_crc": perfil.desviacion_crc,
        "num_transferencias": perfil.num_transferencias,
        "horario_habitual": {
            "inicio": hhmm(perfil.hora_inicio),
            "fin": hhmm(perfil.hora_fin),
        },
        "destinos_frecuentes": list(perfil.destinos_frecuentes),
        "esquema_version": ESQUEMA_VERSION,
    }
