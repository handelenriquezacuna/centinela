"""H-21 / T-76 sin base de datos: la definicion del perfil, el pipeline y el disparador D3."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.deteccion.d3 import d3
from app.deteccion.perfiles import calcular_perfil, ventana_horaria
from app.nucleo.config import AjustesIndicadores, AjustesPerfiles
from app.repos.perfiles import construir_pipeline
from app.supervision import Supervisor

RAIZ = Path(__file__).resolve().parents[2]


def _en_cr(hora: int, minuto: int = 0, dia: int = 10) -> datetime:
    """Un instante en UTC que es la hora `hora` de Costa Rica (UTC-6)."""
    return datetime(2026, 11, dia, hora, minuto, tzinfo=UTC) + timedelta(hours=6)


def _histograma(**horas: int) -> list[int]:
    resultado = [0] * 24
    for hora, cuantas in horas.items():
        resultado[int(hora.removeprefix("h"))] = cuantas
    return resultado


# ---------------------------------------------------------------------------
# Horario habitual
# ---------------------------------------------------------------------------


def prueba_el_horario_es_la_ventana_mas_corta_con_el_90_por_ciento() -> None:
    # 100 transferencias: 95 de 9 a 17 horas y 5 sueltas de madrugada.
    histograma = _histograma(h9=20, h10=20, h11=20, h14=20, h17=15, h3=5)
    assert ventana_horaria(histograma) == (9, 17)


def prueba_la_ventana_puede_cruzar_la_medianoche() -> None:
    histograma = _histograma(h22=30, h23=30, h0=20, h1=15, h12=5)
    assert ventana_horaria(histograma) == (22, 1)


def prueba_una_sola_hora_basta_y_dos_extremos_dan_la_ventana_circular_corta() -> None:
    assert ventana_horaria(_histograma(h8=10)) == (8, 8)
    # 20:00 a 05:59 son 10 horas pasando por la medianoche; de 05:00 a 20:59 serian 16.
    assert ventana_horaria(_histograma(h5=5, h20=5)) == (20, 5)


def prueba_un_cliente_que_opera_a_toda_hora_cubre_casi_todo_el_dia() -> None:
    # 90 % de 24 horas parejas son 21,6: hacen falta 22 horas, y gana la que empieza antes.
    assert ventana_horaria([10] * 24) == (0, 21)


# ---------------------------------------------------------------------------
# calcular_perfil
# ---------------------------------------------------------------------------


def prueba_el_promedio_es_entero_y_redondea_la_mitad_hacia_arriba() -> None:
    perfil = calcular_perfil([(10_001, "1111-1111", _en_cr(9)), (10_002, "1111-1111", _en_cr(9))])
    assert perfil is not None
    assert perfil["promedio_crc"] == 10_002  # 10.001,5 -> 10.002
    assert isinstance(perfil["promedio_crc"], int)
    assert perfil["maximo_crc"] == 10_002
    assert perfil["num_transferencias"] == 2


def prueba_la_desviacion_es_poblacional_y_redondeada() -> None:
    montos = [10_000, 20_000, 30_000, 40_000]
    perfil = calcular_perfil([(m, "1111-1111", _en_cr(10)) for m in montos])
    assert perfil is not None
    assert perfil["desviacion_crc"] == 11_180  # sqrt(125.000.000) = 11.180,3
    solo = calcular_perfil([(5_000, "1111-1111", _en_cr(10))])
    assert solo is not None and solo["desviacion_crc"] == 0


def prueba_destinos_frecuentes_con_dos_o_mas_y_si_ninguno_se_repite_el_mas_usado() -> None:
    destinos = ["2222-0002"] * 3 + ["1111-0001"] * 3 + ["3333-0003"] * 2 + ["4444-0004"]
    perfil = calcular_perfil([(1_000, d, _en_cr(10)) for d in destinos])
    assert perfil is not None
    assert perfil["destinos_frecuentes"] == ["1111-0001", "2222-0002", "3333-0003"]

    sin_repetidos = calcular_perfil(
        [(1_000, "9999-0009", _en_cr(10)), (1_000, "5555-0005", _en_cr(11))]
    )
    assert sin_repetidos is not None
    assert sin_repetidos["destinos_frecuentes"] == ["5555-0005"], "empate: el numero menor"


def prueba_el_horario_se_mide_en_hora_de_costa_rica_no_en_utc() -> None:
    # 22:47 en CR es 04:47 UTC del dia siguiente: el perfil tiene que decir 22, no 4.
    perfil = calcular_perfil([(1_000, "1111-1111", _en_cr(22, 47))] * 3)
    assert perfil is not None
    assert perfil["horario_habitual"] == {"inicio": "22:00", "fin": "22:59"}


def prueba_sin_transferencias_no_hay_perfil() -> None:
    assert calcular_perfil([]) is None


# ---------------------------------------------------------------------------
# El pipeline
# ---------------------------------------------------------------------------


def prueba_el_pipeline_cierra_con_merge_por_cliente_y_excluye_alertas_vivas() -> None:
    desde = datetime(2026, 8, 1, tzinfo=UTC)
    corte = datetime(2026, 10, 30, tzinfo=UTC)
    pipeline = construir_pipeline(desde, corte)
    assert pipeline[-1]["$merge"] == {
        "into": "perfiles_comportamiento",
        "on": "_id",
        "whenMatched": "replace",
        "whenNotMatched": "insert",
    }
    assert pipeline[0]["$match"] == {"fecha": {"$gte": desde, "$lt": corte}}
    alertas = next(e["$lookup"] for e in pipeline if e.get("$lookup", {}).get("from") == "alertas")
    assert alertas["pipeline"][0] == {"$match": {"estado": {"$ne": "descartada"}}}
    # Ninguna etapa lee la etiqueta oculta de H-10.
    assert "simulacion" not in json.dumps(pipeline, default=str)

    uno = construir_pipeline(desde, corte, ["CTA-0001", "CTA-0002"])
    assert uno[0]["$match"]["cuenta_origen"] == {"$in": ["CTA-0001", "CTA-0002"]}


# ---------------------------------------------------------------------------
# D3
# ---------------------------------------------------------------------------


class _RepoIndicadoresFalso:
    async def recalcular(self, desde: date, hasta: date) -> int:
        return 1


class _RepoPerfilesFalso:
    def __init__(self) -> None:
        self.llamadas: list[dict[str, object]] = []

    async def recalcular(self, corte: datetime, *, dias: int = 90, cliente_id: str | None = None) -> int:
        self.llamadas.append({"corte": corte, "dias": dias})
        return 1


@pytest.mark.asyncio
async def prueba_d3_recalcula_perfiles_por_su_intervalo_y_no_en_cada_vuelta() -> None:
    supervisor = Supervisor(tolerancia_segundos=10)
    perfiles = _RepoPerfilesFalso()
    ahora_falso = [0.0]
    vueltas = [0]

    class _Indicadores(_RepoIndicadoresFalso):
        async def recalcular(self, desde: date, hasta: date) -> int:
            vueltas[0] += 1
            ahora_falso[0] += 1.0  # cada vuelta de D3 "dura" un segundo del reloj falso
            return 1

    supervisor.lanzar(
        "d3_indicadores",
        d3(
            _Indicadores(),
            supervisor,
            ajustes=AjustesIndicadores(intervalo_segundos=0.01),
            hoy=lambda: date(2026, 11, 22),
            repo_perfiles=perfiles,
            ajustes_perfiles=AjustesPerfiles(activo=True, intervalo_segundos=60, dias=45),
            reloj=lambda: ahora_falso[0],
        ),
    )
    await asyncio.sleep(0.2)
    await supervisor.detener()

    assert vueltas[0] > 5, "D3 dio varias vueltas"
    assert len(perfiles.llamadas) == 1, "un recalculo de perfiles, no uno por vuelta"
    assert perfiles.llamadas[0]["dias"] == 45


@pytest.mark.asyncio
async def prueba_d3_sin_repo_de_perfiles_no_los_toca() -> None:
    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar(
        "d3_indicadores",
        d3(
            _RepoIndicadoresFalso(),
            supervisor,
            ajustes=AjustesIndicadores(intervalo_segundos=0.01),
            hoy=lambda: date(2026, 11, 22),
        ),
    )
    await asyncio.sleep(0.05)
    assert supervisor.informe().tarea("d3_indicadores").ultimo_error is None
    await supervisor.detener()


def prueba_el_recalculo_automatico_de_perfiles_viene_apagado() -> None:
    from app.nucleo.config import Ajustes

    ajustes = Ajustes(mongo={"uri": "mongodb://localhost:27999/?replicaSet=rsfraude"})
    assert ajustes.perfiles.activo is False
    assert ajustes.perfiles.intervalo_segundos == 3600 and ajustes.perfiles.dias == 90


# ---------------------------------------------------------------------------
# tareas.py procedimientos
# ---------------------------------------------------------------------------


@pytest.fixture
def tareas(monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location("tareas_t76", RAIZ / "tareas.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["tareas_t76"] = modulo
    spec.loader.exec_module(modulo)
    monkeypatch.setattr(modulo, "_buscar", lambda programa: programa)
    for variable in modulo.USUARIOS.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv(modulo.VARIABLE_URI, "mongodb://localhost:27372/?replicaSet=rsfraude")
    monkeypatch.delenv("CENTINELA_MONGO__BASE", raising=False)
    yield modulo
    sys.modules.pop("tareas_t76", None)


def prueba_procedimientos_sin_nombre_corre_los_cinco_en_orden(tareas, capsys) -> None:
    assert tareas.main(["--dry-run", "procedimientos"]) == 0
    lineas = [x for x in capsys.readouterr().out.splitlines() if x.startswith("$ ")]
    assert [Path(linea.split("--file ")[1]).name for linea in lineas] == [
        "01-recalcular-indicadores.js",
        "02-recalcular-rasgos-grafo.js",
        "03-recalcular-perfiles.js",
        "04-reporte-efectividad.js",
        "05-map-reduce-comparativo.js",
    ]
    assert all("27372" in linea and "27018" not in linea for linea in lineas)


def prueba_procedimientos_pasa_el_rango_solo_al_script_de_indicadores(tareas, capsys) -> None:
    assert tareas.main(
        ["--dry-run", "procedimientos", "01", "--desde", "2026-11-01", "--hasta", "2026-11-22"]
    ) == 0
    salida = capsys.readouterr().out
    assert 'DESDE = "2026-11-01"; HASTA = "2026-11-22"' in salida
    assert "01-recalcular-indicadores.js" in salida and "02-" not in salida

    assert tareas.main(["--dry-run", "procedimientos", "03-recalcular-perfiles"]) == 0
    assert "DESDE" not in capsys.readouterr().out


def prueba_procedimientos_rechaza_un_nombre_desconocido_y_un_rango_a_medias(tareas, capsys) -> None:
    assert tareas.main(["--dry-run", "procedimientos", "99"]) == 1
    assert "no hay un procedimiento '99'" in capsys.readouterr().err
    assert tareas.main(["--dry-run", "procedimientos", "01", "--desde", "2026-11-01"]) == 1
    assert "van juntos" in capsys.readouterr().err
