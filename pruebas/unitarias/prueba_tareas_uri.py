"""T-53: con CENTINELA_MONGO__URI apuntando a otro nodo, tareas.py no toca la base viva.

El incidente: los pasos de mongosh tomaban el host del YAML (localhost:27018) e
ignoraban la variable, y un agente que apuntaba a su nodo de prueba recreo las
colecciones de la base viva. Estas pruebas no abren ninguna conexion: solo miran
las ordenes que el lanzador armaria.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
NODO_PRUEBA = "mongodb://localhost:27172/?replicaSet=rsfraude"


@pytest.fixture
def tareas(monkeypatch):
    spec = importlib.util.spec_from_file_location("tareas_t53", RAIZ / "tareas.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["tareas_t53"] = modulo
    spec.loader.exec_module(modulo)
    monkeypatch.setattr(modulo, "_buscar", lambda programa: programa)

    def sin_yaml(*_a, **_k):
        raise AssertionError("con la variable exportada no se debe leer el YAML")

    monkeypatch.setattr(modulo, "_salida", sin_yaml)
    for variable in modulo.USUARIOS.values():
        monkeypatch.delenv(variable, raising=False)
    yield modulo
    sys.modules.pop("tareas_t53", None)


def _ordenes(tareas):
    return [
        tareas.mongosh("--file", "x.js"),
        tareas.mongosh("--file", "x.js", usuario="centinela_admin"),
        tareas.mongosh_directo("--eval", "1"),
        [tareas.uri_directa("centinela_admin")],
    ]


def prueba_la_variable_manda_sobre_el_yaml(tareas, monkeypatch):
    monkeypatch.setenv(tareas.VARIABLE_URI, NODO_PRUEBA)
    for orden in _ordenes(tareas):
        texto = " ".join(orden)
        assert "27172" in texto
        assert "27018" not in texto


def prueba_la_variable_con_usuario_se_usa_tal_cual(tareas, monkeypatch):
    uri = "mongodb://yo:secreto@localhost:27172/antifraude?replicaSet=rsfraude"
    monkeypatch.setenv(tareas.VARIABLE_URI, uri)
    assert tareas.uri_usuario("centinela_admin") == uri
    assert "yo:secreto" not in tareas.uri_base()
    assert "27018" not in tareas.uri_directa()


def prueba_sin_variable_se_usa_el_yaml(tareas, monkeypatch):
    monkeypatch.delenv(tareas.VARIABLE_URI, raising=False)
    yaml = "mongodb://localhost:27018,localhost:27019/?replicaSet=rsfraude"
    monkeypatch.setattr(tareas, "_salida", lambda *_a, **_k: yaml)
    assert tareas.uri_base() == yaml
    assert "localhost:27018" in tareas.uri_directa()
