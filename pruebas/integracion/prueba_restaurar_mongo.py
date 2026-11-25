"""D-74: `python tareas.py restaurar` contra MongoDB de verdad (y sus guardas sin Mongo).

Restaurar es lo unico que reemplaza datos reales, por eso es un comando y no una ruta. La
prueba corre la tarea con `tareas.main([...])` sobre una base propia
(`antifraude_pruebas_respaldos`, via `--base`): hace un respaldo, rompe la base, restaura, y
comprueba que los conteos vuelven y que quedo el respaldo previo automatico.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from app.repos.carga_demo import cargar_demo
from pruebas.conftest import saltar_o_fallar
from pruebas.dobles.datos_demo import DATOS_DEMO

pytestmark = pytest.mark.mongo

RAIZ = Path(__file__).resolve().parents[2]
BASE = "antifraude_pruebas_respaldos"


@pytest.fixture
def tareas(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ajustes: Any) -> Iterator[Any]:
    spec = importlib.util.spec_from_file_location("tareas_restaurar", RAIZ / "tareas.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["tareas_restaurar"] = modulo
    spec.loader.exec_module(modulo)
    monkeypatch.setattr(modulo, "DIRECTORIO_RESPALDOS", tmp_path / "respaldos")
    monkeypatch.setattr(modulo, "ARCHIVO_ENV", tmp_path / "no-hay.env")
    monkeypatch.setenv("CENTINELA_MONGO__URI", ajustes.mongo.uri)
    for variable in modulo.USUARIOS.values():
        monkeypatch.delenv(variable, raising=False)
    modulo._URI_BASE.clear()
    yield modulo
    sys.modules.pop("tareas_restaurar", None)


@pytest_asyncio.fixture
async def base_con_datos(base_mongo: Any) -> Any:
    for programa in ("mongodump", "mongorestore", "mongosh"):
        if shutil.which(programa) is None:
            saltar_o_fallar(f"{programa} no esta en el PATH")
    await base_mongo.client.drop_database(BASE)
    base = base_mongo.client[BASE]
    await cargar_demo(base, DATOS_DEMO)
    yield base
    await base_mongo.client.drop_database(BASE)


async def _conteos(base: Any) -> dict[str, int]:
    return {
        n: await base[n].count_documents({})
        for n in sorted(await base.list_collection_names(filter={"type": "collection"}))
        if not n.startswith("system.")
    }


def _respaldar(base: str, uri: str, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["mongodump", f"--uri={uri}", f"--archive={destino}", "--gzip", f"--db={base}"],
        check=True, capture_output=True,
    )
    return destino


@pytest.mark.asyncio
async def prueba_restaurar_devuelve_los_datos_y_deja_un_respaldo_previo(
    tareas: Any, base_con_datos: Any, ajustes: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = await _conteos(base_con_datos)
    archivo = _respaldar(BASE, ajustes.mongo.uri, tmp_path / "respaldos" / "RSP-20261124T153012Z.gz")
    await base_con_datos["transacciones"].delete_many({})
    await base_con_datos["alertas"].delete_one({})
    assert await _conteos(base_con_datos) != original

    codigo = tareas.main(["restaurar", "--archivo", str(archivo), "--base", BASE, "--si"])

    salida = capsys.readouterr().out
    assert codigo == 0, salida
    assert await _conteos(base_con_datos) == original
    previos = list((tmp_path / "respaldos").glob("previo-restauracion-*.gz"))
    assert len(previos) == 1, "se hizo el respaldo previo automatico"
    assert "antes:" in salida and "despues:" in salida


@pytest.mark.asyncio
async def prueba_el_respaldo_previo_es_la_vuelta_atras(
    tareas: Any, base_con_datos: Any, ajustes: Any, tmp_path: Path
) -> None:
    """Restaurar un respaldo VIEJO pierde lo nuevo; el previo lo devuelve."""
    archivo = _respaldar(BASE, ajustes.mongo.uri, tmp_path / "respaldos" / "viejo.gz")
    await base_con_datos["transacciones"].delete_many({})
    despues_de_borrar = await _conteos(base_con_datos)

    assert tareas.main(["restaurar", "--archivo", str(archivo), "--base", BASE, "--si"]) == 0
    previo = next((tmp_path / "respaldos").glob("previo-restauracion-*.gz"))
    assert (await _conteos(base_con_datos))["transacciones"] > 0

    assert tareas.main(["restaurar", "--archivo", str(previo), "--base", BASE, "--si"]) == 0
    assert await _conteos(base_con_datos) == despues_de_borrar


@pytest.mark.asyncio
async def prueba_con_el_manifiesto_compara_y_un_manifiesto_distinto_falla(
    tareas: Any, base_con_datos: Any, ajustes: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archivo = _respaldar(BASE, ajustes.mongo.uri, tmp_path / "respaldos" / "RSP-20261124T153012Z.gz")
    conteos = await _conteos(base_con_datos)
    manifiesto = {
        "base": BASE,
        "documentos_por_coleccion": conteos,
        "documentos_al_terminar": conteos,
    }
    archivo.with_suffix(".json").write_text(json.dumps(manifiesto))

    assert tareas.main(["restaurar", "--archivo", str(archivo), "--base", BASE, "--si"]) == 0
    assert "coincide con el manifiesto" in capsys.readouterr().out

    manifiesto["documentos_por_coleccion"] = manifiesto["documentos_al_terminar"] = {
        **conteos, "alertas": conteos["alertas"] + 3,
    }
    archivo.with_suffix(".json").write_text(json.dumps(manifiesto))
    assert tareas.main(["restaurar", "--archivo", str(archivo), "--base", BASE, "--si"]) == 1
    assert "no coinciden con el manifiesto" in capsys.readouterr().err


def prueba_sin_si_no_hace_nada(
    tareas: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archivo = tmp_path / "respaldos" / "x.gz"
    archivo.parent.mkdir()
    archivo.write_bytes(b"x")

    codigo = tareas.main(["restaurar", "--archivo", str(archivo)])

    assert codigo == 1
    assert "Falta confirmar con --si" in capsys.readouterr().err
    assert not list((tmp_path / "respaldos").glob("previo-*")), "ni el respaldo previo"


def prueba_un_archivo_que_no_existe_dice_donde_se_busco(
    tareas: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert tareas.main(["restaurar", "--archivo", "RSP-20200101T000000Z", "--si"]) == 1
    assert "no se encontro el archivo de respaldo" in capsys.readouterr().err
