"""T-57 · Carga de transacciones desde JSONL: el parser y el armado de lotes, sin Mongo.

La integracion con la base (duplicados, validador) la prueba
`pruebas/integracion/prueba_carga_mongo.py`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.carga.lotes import LineaInvalida, cargar, interpretar_linea
from app.repos.carga_transacciones import ResultadoLote


def _linea(numero: int = 1, **cambios: Any) -> str:
    base: dict[str, Any] = {
        "_id": f"TXN-{numero:06d}",
        "cuenta_origen": "CTA-001",
        "cuenta_destino": "6033-9001",
        "monto_crc": 150_000,
        "fecha": "2026-11-23T14:05:00-06:00",
    }
    base.update(cambios)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def prueba_una_linea_valida_queda_en_utc_con_dinero_entero() -> None:
    transaccion = interpretar_linea(_linea())

    documento = transaccion.a_documento()
    assert documento["_id"] == "TXN-000001"
    assert documento["monto_crc"] == 150_000 and isinstance(documento["monto_crc"], int)
    assert documento["moneda"] == "CRC"
    assert documento["fecha"].utcoffset().total_seconds() == 0
    assert documento["fecha"].hour == 20  # 14:05 en Costa Rica es 20:05 UTC
    assert documento["esquema_version"] >= 1


@pytest.mark.parametrize(
    ("texto", "pista"),
    [
        ("esto no es json", "no es JSON valido"),
        ("[1, 2]", "objeto JSON"),
        (_linea(monto_crc=1500.5), "monto_crc"),
        (_linea(monto_crc="150000"), "monto_crc"),
        (_linea(monto_crc=-5), "monto_crc"),
        (_linea(fecha="2026-11-23T14:05:00"), "fecha"),
        (_linea(fecha="ayer"), "fecha"),
        (_linea(_id="PAGO-1"), "_id"),
        (_linea(cuenta_destino="123"), "cuenta_destino"),
        (_linea(moneda="USD"), "moneda"),
        (json.dumps({"_id": "TXN-000001"}), "cuenta_origen"),
    ],
)
def prueba_una_linea_invalida_se_rechaza_diciendo_por_que(texto: str, pista: str) -> None:
    with pytest.raises(LineaInvalida) as fallo:
        interpretar_linea(texto)
    assert pista in str(fallo.value)


# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------


class RepoCargaFalso:
    """Guarda los lotes que recibe. `repetidas` y `errores` simulan lo que dice la base."""

    def __init__(self, repetidas: int = 0, errores: list[tuple[int, str]] | None = None) -> None:
        self.lotes: list[list[tuple[int, dict[str, Any]]]] = []
        self._repetidas = repetidas
        self._errores = errores or []

    async def insertar_lote(self, lote: list[tuple[int, dict[str, Any]]]) -> ResultadoLote:
        self.lotes.append(list(lote))
        return ResultadoLote(
            insertadas=len(lote) - self._repetidas - len(self._errores),
            repetidas=self._repetidas,
            errores=list(self._errores),
        )


@pytest.mark.asyncio
async def prueba_los_lotes_son_de_mil_y_el_ultimo_lleva_el_resto() -> None:
    repo = RepoCargaFalso()

    resumen = await cargar(repo, [_linea(n) for n in range(1, 2501)])

    assert [len(lote) for lote in repo.lotes] == [1000, 1000, 500]
    assert resumen.cargadas == 2500
    assert resumen.repetidas == 0 and resumen.rechazadas == []


@pytest.mark.asyncio
async def prueba_las_invalidas_se_rechazan_con_su_numero_de_linea_y_no_frenan_al_resto() -> None:
    repo = RepoCargaFalso()
    lineas = [_linea(1), "basura", _linea(3), "", _linea(5, monto_crc=9.5), _linea(6)]

    resumen = await cargar(repo, lineas)

    assert [numero for numero, _doc in repo.lotes[0]] == [1, 3, 6]
    assert resumen.cargadas == 3
    assert [r.numero for r in resumen.rechazadas] == [2, 5]
    assert "no es JSON valido" in resumen.rechazadas[0].motivo
    assert "monto_crc" in resumen.rechazadas[1].motivo


@pytest.mark.asyncio
async def prueba_las_repetidas_no_son_error_y_los_demas_errores_de_la_base_si() -> None:
    repo = RepoCargaFalso(repetidas=1, errores=[(2, "Document failed validation")])

    resumen = await cargar(repo, [_linea(1), _linea(2), _linea(3)])

    assert (resumen.cargadas, resumen.repetidas) == (1, 1)
    assert [(r.numero, "Document failed validation" in r.motivo) for r in resumen.rechazadas] == [
        (2, True)
    ]


@pytest.mark.asyncio
async def prueba_un_archivo_vacio_no_llama_a_la_base() -> None:
    repo = RepoCargaFalso()

    resumen = await cargar(repo, ["", "   "])

    assert repo.lotes == []
    assert (resumen.cargadas, resumen.repetidas, resumen.rechazadas) == (0, 0, [])
