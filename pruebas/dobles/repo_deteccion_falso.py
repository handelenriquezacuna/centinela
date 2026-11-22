"""Doble del repositorio de D1 (`app/repos/deteccion.py`).

Sirve para lo que una prueba contra Mongo no puede mostrar sin matar el proceso a
mano: el ORDEN de las escrituras. Cada operacion que D1 pide queda en `bitacora`, en
el orden en que la pidio, y la prueba compara contra el contrato: primero la alerta,
despues el token.

Los hechos de contexto son los del caso documentado (TXN-001 de Maria Rodriguez,
puntaje 75): el catalogo y la politica copian `scripts/semilla/03-catalogos.js`, igual
que `pruebas/unitarias/prueba_evaluador.py`.

Cumple `ProtocoloRepoDeteccion` de forma estructural; `prueba_d1.py` lo verifica.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from app.deteccion import ContextoEvaluacion, PoliticaDeteccion, VentanaOrigen
from app.modelos import Alerta, Regla, Transaccion
from app.repos.deteccion import DatosOrigen, EventoTransaccion
from app.repos.flujo_alertas import TokenDeReanudacionInvalido
from pruebas.dobles.datos_demo import PERFILES

REGLAS_DEL_CASO = [
    Regla.model_validate(
        {
            "_id": regla_id,
            "codigo": codigo,
            "descripcion": f"Regla {codigo}",
            "tipo": tipo,
            "umbral": umbral,
            "parametros": parametros,
            "peso": peso,
            "activa": True,
            "version": 1,
        }
    )
    for regla_id, codigo, tipo, umbral, parametros, peso in [
        ("REG-001", "R-01", "monto", 10, {}, 35),
        ("REG-002", "R-03", "destino", 0, {"dias_historial": 90}, 25),
        ("REG-003", "R-05", "horario", 0, {}, 15),
    ]
]

POLITICA_DEL_CASO = PoliticaDeteccion.model_validate(
    {
        "_id": "POL-001",
        "version": 1,
        "umbral_alerta": 40,
        "puntaje_maximo": 100,
        "cortes_severidad": [
            {"severidad": "MEDIA", "puntaje_minimo": 40, "puntaje_maximo": 54},
            {"severidad": "ALTA", "puntaje_minimo": 55, "puntaje_maximo": 69},
            {"severidad": "CRITICA", "puntaje_minimo": 70, "puntaje_maximo": 100},
        ],
    }
)

ORIGEN_DEL_CASO = DatosOrigen(
    cuenta_sinpe="8712-4455", cliente_id="CLI-001", cliente_nombre="Maria Rodriguez Vargas"
)


def token(numero: int) -> dict[str, str]:
    """Token con la forma del real de MongoDB 8: `{"_data": "<hex>"}`."""
    return {"_data": f"82{numero:06X}"}


class RepoDeteccionFalso:
    """Entrega una lista de vueltas y anota cada operacion en `bitacora`.

    - `vueltas`: lo que entrega cada apertura del change stream (al reanudar, solo lo
      posterior al token, igual que `resumeAfter`). Agotadas, la apertura se queda
      esperando para siempre, como un cursor sin trafico; `agotado` avisa a la prueba.
    - `token_guardado`: el token con el que arranca, como si estuviera en Mongo.
    - `rechazar_token`: la primera apertura con token falla como
      `ChangeStreamHistoryLost`.
    - `alertas_existentes`: transacciones que ya tienen alerta (el indice unico).
    - `fallos_al_insertar`: cuantas veces falla `insertar_alerta` antes de funcionar.
    - `reglas`: el catalogo activo; por omision el del caso. `[]` simula el catalogo
      vacio de T-18.
    """

    def __init__(
        self,
        vueltas: Sequence[EventoTransaccion],
        *,
        token_guardado: Mapping[str, Any] | None = None,
        rechazar_token: bool = False,
        alertas_existentes: Sequence[str] = (),
        fallos_al_insertar: int = 0,
        reglas: Sequence[Regla] | None = None,
    ) -> None:
        self._vueltas = list(vueltas)
        self._reglas = list(REGLAS_DEL_CASO if reglas is None else reglas)
        self.token_guardado = token_guardado
        self._rechazar_token = rechazar_token
        self.alertas: dict[str, Alerta] = {}
        self._existentes = set(alertas_existentes)
        self._fallos_al_insertar = fallos_al_insertar
        self.bitacora: list[str] = []
        self.aperturas: list[Mapping[str, Any] | None] = []
        self.descartes: list[dict[str, Any]] = []
        self.agotado = asyncio.Event()

    # -- Change stream ----------------------------------------------------

    async def observar_transacciones(
        self, *, desde_token: Mapping[str, Any] | None, espera_ms: int
    ) -> AsyncIterator[EventoTransaccion]:
        self.aperturas.append(desde_token)
        if desde_token is not None and self._rechazar_token:
            self._rechazar_token = False
            raise TokenDeReanudacionInvalido(
                "el token de reanudacion de transacciones ya no sirve (ChangeStreamHistoryLost)"
            )
        pendientes = self._vueltas
        if desde_token is not None:
            for indice, vuelta in enumerate(self._vueltas):
                if vuelta.token == desde_token:
                    pendientes = self._vueltas[indice + 1 :]
                    break
        for vuelta in pendientes:
            yield vuelta
        self.agotado.set()
        await asyncio.Event().wait()

    # -- Lecturas ---------------------------------------------------------

    async def leer_reglas_activas(self) -> list[Regla]:
        return list(self._reglas)

    async def leer_politica_vigente(self) -> PoliticaDeteccion:
        return POLITICA_DEL_CASO

    async def leer_origen(self, transaccion: Transaccion) -> DatosOrigen:
        return ORIGEN_DEL_CASO

    async def armar_contexto(
        self, transaccion: Transaccion, origen: DatosOrigen, reglas: Sequence[Regla]
    ) -> ContextoEvaluacion:
        return ContextoEvaluacion(
            perfil=PERFILES[0],
            dias_historial=90,
            destinos_conocidos={},
            entradas_lista_riesgo=(),
            ventana_origen=VentanaOrigen(
                ventana_minutos=30, cantidad=1, monto_acumulado_crc=transaccion.monto_crc
            ),
        )

    # -- Escrituras -------------------------------------------------------

    async def insertar_alerta(self, alerta: Alerta) -> bool:
        if self._fallos_al_insertar > 0:
            self._fallos_al_insertar -= 1
            self.bitacora.append(f"fallo_insertar:{alerta.transaccion_id}")
            raise ConnectionError("la base no contesta")
        if alerta.transaccion_id in self._existentes:
            self.bitacora.append(f"duplicada:{alerta.transaccion_id}")
            return False
        self._existentes.add(alerta.transaccion_id)
        self.alertas[alerta.transaccion_id] = alerta
        self.bitacora.append(f"alerta:{alerta.transaccion_id}")
        return True

    async def leer_token(self, disparador: str) -> Mapping[str, Any] | None:
        return self.token_guardado

    async def guardar_token(
        self,
        disparador: str,
        token: Mapping[str, Any],
        *,
        transaccion_id: str | None,
    ) -> None:
        self.token_guardado = token
        self.bitacora.append(f"token:{transaccion_id or 'vacio'}")

    async def descartar(
        self, disparador: str, *, transaccion_id: str, motivo: str, intentos: int
    ) -> None:
        self.descartes.append(
            {"transaccion_id": transaccion_id, "motivo": motivo, "intentos": intentos}
        )
        self.bitacora.append(f"descarte:{transaccion_id}")
