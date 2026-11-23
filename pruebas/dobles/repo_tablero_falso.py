"""Dobles de `RepoTablero` (H-20) y de `RepoRedMulas` (T-35), en memoria."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.repos.listas_riesgo import (
    EntradaNoEncontrada,
    ResultadoAprobacion,
    motivo_de_red,
)
from app.repos.red_mulas import (
    AristaRed,
    CuentaNoEncontrada,
    NodoRed,
    RedMulas,
)
from app.repos.tablero import resumir


def indicador(
    fecha: str,
    total: int,
    severidad: tuple[int, int, int],
    estado: tuple[int, int, int, int, int],
    **extra: Any,
) -> dict[str, Any]:
    media, alta, critica = severidad
    return {
        "_id": fecha,
        "fecha": fecha,
        "total_alertas": total,
        "por_severidad": {"MEDIA": media, "ALTA": alta, "CRITICA": critica},
        "por_estado": dict(
            zip(("nueva", "en_revision", "confirmada", "descartada", "escalada"), estado, strict=True)
        ),
        "esquema_version": 1,
        **extra,
    }


class RepoTableroFalso:
    def __init__(self, documentos: list[dict[str, Any]] | None = None) -> None:
        self.documentos = documentos or []

    async def leer(self, dias: int) -> dict[str, Any]:
        return resumir(self.documentos, dias)


def documentos_con_comparacion() -> list[dict[str, Any]]:
    """Dos dias previos y dos actuales (periodo de 2 dias), con agente y hora."""
    marca = datetime(2026, 11, 20, 3, 30, tzinfo=UTC)  # 21:30 del 19 en Costa Rica
    horas = {"09": 3, "22": 5}
    return [
        indicador("2026-11-16", 4, (2, 1, 1), (0, 0, 2, 2, 0)),
        indicador("2026-11-17", 6, (3, 2, 1), (0, 0, 4, 2, 0)),
        indicador(
            "2026-11-18", 10, (4, 3, 3), (2, 1, 5, 1, 1),
            por_agente={"AGT-001": 6, "AGT-002": 4}, por_hora=horas, calculado_en=marca - timedelta(days=1),
        ),
        indicador(
            "2026-11-19", 5, (1, 2, 2), (1, 0, 3, 1, 0),
            por_agente={"AGT-001": 3, "AGT-002": 2}, por_hora={"22": 5}, calculado_en=marca,
        ),
    ]


class RepoRedMulasFalso:
    def __init__(self, red: RedMulas | None = None) -> None:
        self.red = red
        self.pedidos: list[dict[str, Any]] = []

    async def recorrer(self, cuenta: str, **opciones: Any) -> RedMulas:
        self.pedidos.append({"cuenta": cuenta, **opciones})
        if self.red is None:
            raise CuentaNoEncontrada(f"la cuenta {cuenta} no existe")
        return self.red


def red_de_cuatro_saltos() -> RedMulas:
    """victima -> mula 1 -> mula 2 -> mula 3 -> cuenta externa: 4 saltos, montos que bajan."""
    cuentas = ["8712-4455", "6033-9001", "6033-9002", "6033-9003", "7000-0001"]
    base = datetime(2026, 11, 19, 3, 47, tzinfo=UTC)
    montos = [750_000, 740_000, 720_000, 700_000]
    minutos = [None, 12.5, 8.0, 21.0]
    aristas = [
        AristaRed(
            transaccion_id=f"TXN-10{i}",
            origen=cuentas[i],
            destino=cuentas[i + 1],
            monto_crc=montos[i],
            fecha=base + timedelta(minutes=10 * i),
            salto=i + 1,
            minutos_desde_anterior=minutos[i],
        )
        for i in range(4)
    ]
    nodos = [
        NodoRed(
            cuenta=c,
            cuenta_id=None if c == "7000-0001" else f"CTA-00{i + 1}",
            externa=c == "7000-0001",
            nivel=i,
            en_lista_riesgo=c == "6033-9001",
        )
        for i, c in enumerate(cuentas)
    ]
    return RedMulas(
        cuenta=cuentas[0],
        desde=base,
        ventana_minutos=60,
        saltos_maximos=6,
        saltos_recorridos=4,
        truncada=False,
        nodos=nodos,
        aristas=aristas,
        propuestas_lista_riesgo=["6033-9002", "6033-9003", "7000-0001"],
    )


class RepoListasRiesgoFalso:
    """`RepoListasRiesgo` en memoria: las mismas reglas (idempotencia, 404) sin Mongo."""

    def __init__(self, entradas: list[dict[str, Any]] | None = None) -> None:
        self.entradas = entradas if entradas is not None else []
        self.bajas: list[tuple[str, str]] = []

    async def entradas_de_cuentas(self, cuentas: list[str]) -> dict[str, list[dict[str, Any]]]:
        por_cuenta: dict[str, list[dict[str, Any]]] = {}
        for entrada in self.entradas:
            if entrada["cuenta"] in cuentas:
                por_cuenta.setdefault(entrada["cuenta"], []).append(entrada)
        return por_cuenta

    async def aprobar(
        self, cuenta: str, *, cuenta_partida: str, agente_id: str, ahora: datetime | None = None
    ) -> ResultadoAprobacion:
        for entrada in self.entradas:
            if entrada["cuenta"] == cuenta and entrada["vigente"]:
                return ResultadoAprobacion(entrada, creada=False)
        nueva = {
            "_id": f"RSK-{len(self.entradas) + 1:03d}",
            "cuenta": cuenta,
            "motivo": motivo_de_red(cuenta_partida),
            "fecha_reporte": ahora or datetime.now(UTC),
            "vigente": True,
            "esquema_version": 1,
            "aprobo": agente_id,
        }
        self.entradas.append(nueva)
        return ResultadoAprobacion({k: v for k, v in nueva.items() if k != "aprobo"}, creada=True)

    async def dar_de_baja(self, riesgo_id: str, *, agente_id: str) -> dict[str, Any]:
        for posicion, entrada in enumerate(self.entradas):
            if entrada["_id"] == riesgo_id:
                self.bajas.append((riesgo_id, agente_id))
                return self.entradas.pop(posicion)
        raise EntradaNoEncontrada(f"No existe la entrada {riesgo_id}")
