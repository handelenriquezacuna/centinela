"""Dobles de los repositorios, en memoria.

Sirven para que las pruebas de contrato de la API corran sin Mongo arriba. El doble
implementa `ProtocoloRepoAlertas` de forma estructural: si la firma del repositorio
real cambia, `prueba_el_doble_cumple_el_protocolo` lo dice.

El doble filtra en Python lo mismo que el repositorio real delega a Mongo. Es una
duplicacion consciente y acotada: si los dos se separan, la prueba marcada `mongo`
compara los dos caminos sobre los mismos datos y el desacuerdo sale a la luz.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from app.deteccion import PoliticaDeteccion
from app.modelos import EstadoAlerta, Severidad
from app.modelos.base import asumir_utc
from app.repos.alertas import ConflictoAlerta, PlanTransicion
from app.repos.reglas import CodigoDuplicado, instantanea, siguiente_regla_id


class RepoAlertasFalso:
    """Repositorio de alertas en memoria.

    Para H-14 aplica en Python el plan que arma `planear_transicion` (la misma funcion
    pura que usa el real): el filtro condicionado, el `$set` y el `$push`. No
    reimplementa la regla, solo la escritura.

    - `interferir_antes_de_transicionar`: simula a otro agente que la toma justo antes
      de que llegue la escritura (el filtro deja de coincidir).
    - `casos`: los casos creados al escalar, por `_id`.
    """

    def __init__(self, documentos: list[dict[str, Any]]) -> None:
        # Copia: una prueba que modifique un documento no debe ensuciar a la que sigue.
        self._documentos = [copy.deepcopy(documento) for documento in documentos]
        self.llamadas: list[dict[str, Any]] = []
        self.casos: dict[str, dict[str, Any]] = {}
        self.interferir_antes_de_transicionar = False
        self.escrituras = 0

    async def obtener(self, alerta_id: str) -> dict[str, Any] | None:
        documento = next((d for d in self._documentos if d["_id"] == alerta_id), None)
        return copy.deepcopy(documento) if documento is not None else None

    async def aplicar_transicion(self, plan: PlanTransicion) -> dict[str, Any]:
        guardado = next(d for d in self._documentos if d["_id"] == plan.alerta_id)
        if self.interferir_antes_de_transicionar:
            guardado["estado"] = "en_revision"
            guardado["agente_id"] = "AGT-999"
        if not self._coincide(guardado, plan.filtro):
            raise ConflictoAlerta(f"La alerta {plan.alerta_id} cambio mientras se resolvia")
        if plan.caso is not None and plan.caso["_id"] in self.casos:
            raise ConflictoAlerta(f"Ya existe el caso {plan.caso['_id']}")
        guardado.update(copy.deepcopy(plan.actualizacion["$set"]))
        guardado["historial"] = [
            *guardado["historial"],
            copy.deepcopy(plan.actualizacion["$push"]["historial"]),
        ]
        if plan.caso is not None:
            self.casos[plan.caso["_id"]] = copy.deepcopy(plan.caso)
        self.escrituras += 1
        return copy.deepcopy(guardado)

    @staticmethod
    def _coincide(documento: dict[str, Any], filtro: dict[str, Any]) -> bool:
        for campo, esperado in filtro.items():
            if isinstance(esperado, dict) and esperado == {"$exists": False}:
                if campo in documento:
                    return False
            elif documento.get(campo) != esperado:
                return False
        return True

    async def listar(
        self,
        *,
        estado: EstadoAlerta | None = None,
        severidad: Severidad | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: int = 50,
        desplazamiento: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        # Se registra lo que llego para poder afirmar que la ruta aplico el acotado
        # del limite ANTES de consultar, y no despues de traer todo.
        self.llamadas.append(
            {
                "estado": estado,
                "severidad": severidad,
                "desde": desde,
                "hasta": hasta,
                "limite": limite,
                "desplazamiento": desplazamiento,
            }
        )

        filtrados = [
            documento
            for documento in self._documentos
            if self._cumple(documento, estado, severidad, desde, hasta)
        ]

        # Mismo orden que ORDEN_PANEL del repositorio real: fecha_creacion
        # descendente, _id ascendente como desempate.
        filtrados.sort(key=lambda documento: documento["_id"])
        filtrados.sort(key=lambda documento: documento["fecha_creacion"], reverse=True)

        total = len(filtrados)
        pagina = filtrados[desplazamiento : desplazamiento + limite]
        return pagina, total

    @staticmethod
    def _cumple(
        documento: dict[str, Any],
        estado: EstadoAlerta | None,
        severidad: Severidad | None,
        desde: datetime | None,
        hasta: datetime | None,
    ) -> bool:
        if estado is not None and documento["estado"] != EstadoAlerta(estado).value:
            return False
        if severidad is not None and documento["severidad"] != Severidad(severidad).value:
            return False

        fecha = asumir_utc(documento["fecha_creacion"])
        if desde is not None and fecha < asumir_utc(desde):
            return False
        if hasta is not None and fecha > asumir_utc(hasta):  # noqa: SIM103 - guardas uniformes
            return False
        return True


class RepoReglasFalso:
    """`reglas_deteccion` en memoria, con la misma regla de versionado que el real.

    La instantanea y el siguiente REG-NNN salen de las funciones puras de
    `app.repos.reglas`, asi que el doble no reimplementa el versionado: solo aplica en
    Python el `$set` y el `$push` que el repositorio real le pide a Mongo.

    `interferir_antes_de_editar` simula a otro supervisor que edita en el medio:
    sube la version guardada justo antes de que llegue la escritura.
    """

    def __init__(
        self,
        documentos: list[dict[str, Any]],
        politica: dict[str, Any] | None = None,
    ) -> None:
        self._documentos = {documento["_id"]: copy.deepcopy(documento) for documento in documentos}
        self._politica = copy.deepcopy(politica)
        self.interferir_antes_de_editar = False
        self.escrituras = 0

    async def listar(self, *, activa: bool | None = None) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(documento)
            for documento in sorted(self._documentos.values(), key=lambda d: d["codigo"])
            if activa is None or documento["activa"] == activa
        ]

    async def obtener(self, regla_id: str) -> dict[str, Any] | None:
        documento = self._documentos.get(regla_id)
        return copy.deepcopy(documento) if documento is not None else None

    async def crear(self, documento: dict[str, Any]) -> dict[str, Any]:
        if any(d["codigo"] == documento["codigo"] for d in self._documentos.values()):
            raise CodigoDuplicado(documento["codigo"])
        nuevo = {"_id": siguiente_regla_id(list(self._documentos)), **documento}
        self._documentos[nuevo["_id"]] = copy.deepcopy(nuevo)
        self.escrituras += 1
        return nuevo

    async def editar(
        self,
        actual: dict[str, Any],
        cambios: dict[str, Any],
        ahora: datetime,
    ) -> dict[str, Any] | None:
        guardado = self._documentos.get(actual["_id"])
        if guardado is not None and self.interferir_antes_de_editar:
            guardado["version"] += 1
        if guardado is None or guardado.get("version") != actual.get("version"):
            return None
        guardado.setdefault("versiones_anteriores", []).append(instantanea(actual, ahora))
        guardado.update(copy.deepcopy(cambios))
        guardado["version"] = (actual.get("version") or 0) + 1
        self.escrituras += 1
        return copy.deepcopy(guardado)

    async def politica_vigente(self) -> PoliticaDeteccion | None:
        """Mismo contrato que `leer_politica_vigente` (T-19): el modelo, o None."""
        if self._politica is None:
            return None
        return PoliticaDeteccion.model_validate(copy.deepcopy(self._politica))

    async def politica_documento(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._politica) if self._politica is not None else None

    async def editar_politica(
        self,
        actual: dict[str, Any],
        cambios: dict[str, Any],
        ahora: datetime,
    ) -> dict[str, Any] | None:
        """Mismo versionado que `RepoReglas.editar_politica`, aplicado en Python."""
        guardada = self._politica
        if guardada is not None and self.interferir_antes_de_editar:
            guardada["version"] += 1
        if guardada is None or guardada.get("version") != actual.get("version"):
            return None
        anterior = instantanea(actual, ahora)
        anterior["vigente"] = False
        guardada.setdefault("versiones_anteriores", []).append(anterior)
        guardada.update(copy.deepcopy(cambios))
        guardada["version"] = (actual.get("version") or 0) + 1
        guardada["vigente_desde"] = ahora
        self.escrituras += 1
        return copy.deepcopy(guardada)
