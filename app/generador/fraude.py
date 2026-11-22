"""H-05 · Inyector de patrones de fraude (vishing).

Tres patrones, todos con la etiqueta oculta `simulacion: {fraude, patron, episodio}`
que SOLO sirve para medir (H-10) y que el motor nunca lee:

- `monto_atipico_destino_nuevo`: una transferencia de 11 a 13 veces el promedio del
  cliente, a una cuenta que nunca uso, normalmente fuera de su horario.
- `rafaga_fragmentada`: de 4 a 6 transferencias seguidas, cada una cerca del tope de la
  entidad, a la misma cuenta nueva, en pocos minutos.
- `cadena_mulas`: la victima transfiere a una cuenta mula del banco y el dinero salta
  por 3 o 4 mulas hasta salir a una cuenta externa; cada eslabon es una transaccion.

Con el tope de SINPE Movil (100.000 a 300.000 segun la entidad), "atipico" quiere decir
atipico respecto del PERFIL del cliente (un multiplo de su promedio), no un monto
enorme. Ningun monto de fraude supera el tope de la entidad de su cuenta de origen.

El presupuesto de fraude es una fraccion del volumen total, siempre menor al 2 %.
"""

from __future__ import annotations

import random
from datetime import timedelta

from app.generador.poblacion import (
    MONTO_MINIMO_CRC,
    HabitosGen,
    PerfilGen,
    Poblacion,
    redondear_monto,
)
from app.generador.transacciones import (
    TxnBorrador,
    elegir_hora,
    instante,
    limite_de_la_historia,
)
from app.nucleo.config import AjustesGenerador

MONTO_ATIPICO = "monto_atipico_destino_nuevo"
RAFAGA = "rafaga_fragmentada"
CADENA = "cadena_mulas"
PATRONES = (MONTO_ATIPICO, RAFAGA, CADENA)

# Multiplo del promedio de la victima en un monto atipico (la regla R-01 pide mas de 10).
MULTIPLO_ATIPICO = (11.0, 13.0)
# Cada fragmento de la rafaga, como fraccion del tope de la entidad de la victima.
FRACCION_FRAGMENTO = (0.85, 0.98)
# Lo que se queda cada mula al pasar el dinero (fraccion que sigue adelante).
FRACCION_QUE_SIGUE = (0.93, 0.98)
# Probabilidad de que el episodio ocurra fuera del horario habitual de la victima.
PROB_FUERA_DE_HORARIO = 0.7
# Reparto de los episodios despues de haber mostrado cada patron una vez.
PESOS_PATRON = (0.5, 0.25, 0.25)


def presupuesto_de_fraude(total: int, proporcion: float) -> int:
    """Cuantas transacciones de fraude caben. Siempre estrictamente menos del 2 %."""
    return int(total * proporcion)


class _Inyector:
    def __init__(
        self,
        poblacion: Poblacion,
        perfiles: list[PerfilGen],
        ajustes: AjustesGenerador,
        rng: random.Random,
    ) -> None:
        self.poblacion = poblacion
        self.ajustes = ajustes
        self.rng = rng
        self.perfiles = {perfil.cliente_id: perfil for perfil in perfiles}
        self.habitos: dict[str, HabitosGen] = {h.cliente_id: h for h in poblacion.habitos}
        self.cuentas = {cuenta.cuenta_id: cuenta for cuenta in poblacion.cuentas}
        mulas = set(poblacion.cuentas_mula)
        self.victimas = [
            cuenta for cuenta in poblacion.cuentas if cuenta.cuenta_id not in mulas
        ]
        self.limite = limite_de_la_historia(ajustes)
        self.episodios = 0

    # -- utilidades ---------------------------------------------------------

    def _inicio_de_episodio(self, victima_cliente: str, holgura_minutos: int):
        habitos = self.habitos[victima_cliente]
        fuera = self.rng.random() < PROB_FUERA_DE_HORARIO
        hora = elegir_hora(habitos, self.rng, fuera_del_horario=fuera)
        inicio = instante(
            self.ajustes, self.rng.randrange(self.ajustes.dias_historia), hora, self.rng
        )
        # Que el episodio entero quepa dentro de la historia, no en el futuro.
        tope = self.limite - timedelta(minutes=holgura_minutos)
        return min(inicio, tope)

    def _txn(self, origen, destino, monto, fecha, patron, episodio) -> TxnBorrador:
        # Defensa en profundidad: el tope de la entidad de origen no se cruza jamas.
        monto = min(monto, self.cuentas[origen].tope_crc)
        return TxnBorrador(origen, destino, monto, fecha, True, patron, episodio)

    def _monto_atipico(self, cuenta_id: str) -> int:
        cuenta = self.cuentas[cuenta_id]
        promedio = self.perfiles[cuenta.cliente_id].promedio_crc
        monto = redondear_monto(promedio * self.rng.uniform(*MULTIPLO_ATIPICO))
        return max(MONTO_MINIMO_CRC, min(monto, cuenta.tope_crc))

    # -- los tres patrones --------------------------------------------------

    def monto_atipico(self, episodio: str) -> list[TxnBorrador]:
        victima = self.rng.choice(self.victimas)
        fecha = self._inicio_de_episodio(victima.cliente_id, 5)
        destino = self.rng.choice(self.poblacion.mulas_externas)
        return [
            self._txn(
                victima.cuenta_id, destino, self._monto_atipico(victima.cuenta_id),
                fecha, MONTO_ATIPICO, episodio,
            )
        ]  # fmt: skip

    def rafaga(self, episodio: str, cantidad: int) -> list[TxnBorrador]:
        victima = self.rng.choice(self.victimas)
        fecha = self._inicio_de_episodio(victima.cliente_id, 30)
        destino = self.rng.choice(self.poblacion.mulas_externas)
        resultado: list[TxnBorrador] = []
        for _ in range(cantidad):
            monto = redondear_monto(victima.tope_crc * self.rng.uniform(*FRACCION_FRAGMENTO))
            resultado.append(self._txn(victima.cuenta_id, destino, monto, fecha, RAFAGA, episodio))
            fecha += timedelta(seconds=self.rng.randint(45, 200))
        return resultado

    def cadena(self, episodio: str, eslabones: int) -> list[TxnBorrador]:
        """Victima -> mula 1 -> ... -> mula N -> cuenta externa. `eslabones` = N."""
        victima = self.rng.choice(self.victimas)
        mulas = [
            self.cuentas[cuenta_id]
            for cuenta_id in self.rng.sample(list(self.poblacion.cuentas_mula), eslabones)
        ]
        fecha = self._inicio_de_episodio(victima.cliente_id, 60)
        monto = self._monto_atipico(victima.cuenta_id)
        resultado = [
            self._txn(
                victima.cuenta_id, mulas[0].cuenta_sinpe, monto, fecha, CADENA, episodio
            )
        ]
        for posicion, mula in enumerate(mulas):
            ultima = posicion == len(mulas) - 1
            destino = (
                self.rng.choice(self.poblacion.mulas_externas)
                if ultima
                else mulas[posicion + 1].cuenta_sinpe
            )
            monto = redondear_monto(monto * self.rng.uniform(*FRACCION_QUE_SIGUE))
            monto = max(MONTO_MINIMO_CRC, min(monto, mula.tope_crc))
            fecha += timedelta(seconds=self.rng.randint(120, 900))
            resultado.append(self._txn(mula.cuenta_id, destino, monto, fecha, CADENA, episodio))
        return resultado

    # -- episodios ----------------------------------------------------------

    def siguiente_episodio(self, patron: str, restante: int) -> list[TxnBorrador]:
        self.episodios += 1
        episodio = f"EPI-{self.episodios:04d}"
        if patron == RAFAGA and restante >= 4:
            return self.rafaga(episodio, self.rng.randint(4, min(6, restante)))
        if patron == CADENA and restante >= 4:
            # 1 salto de la victima + N mulas: N = 3 o 4, y el total cabe en `restante`.
            eslabones = self.rng.randint(3, min(4, restante - 1))
            return self.cadena(episodio, eslabones)
        return self.monto_atipico(episodio)


def inyectar_fraude(
    poblacion: Poblacion,
    perfiles: list[PerfilGen],
    ajustes: AjustesGenerador,
    rng: random.Random,
    presupuesto: int,
) -> list[TxnBorrador]:
    """Episodios de fraude hasta agotar `presupuesto` transacciones.

    Primero un episodio de cada patron (en orden), para que todos aparezcan apenas el
    presupuesto alcance; despues, mezcla segun `PESOS_PATRON`. Si lo que queda no alcanza
    para un episodio largo, se completa con montos atipicos de una sola transaccion.
    """
    inyector = _Inyector(poblacion, perfiles, ajustes, rng)
    fraude: list[TxnBorrador] = []
    turno = 0
    while len(fraude) < presupuesto:
        restante = presupuesto - len(fraude)
        if turno < len(PATRONES):
            patron = PATRONES[turno]
        else:
            patron = rng.choices(PATRONES, weights=PESOS_PATRON, k=1)[0]
        turno += 1
        fraude.extend(inyector.siguiente_episodio(patron, restante))
    return fraude
