"""El modo estricto de las pruebas de Mongo, verificado y no confiado.

`CENTINELA_EXIGIR_MONGO=1` existe porque una vez el CI salio verde con 26 pruebas de
Mongo saltadas: la fixture se saltaba sola al no encontrar la base. Esta prueba corre
una prueba marcada `mongo` en un pytest aparte, apuntando a una URI donde nadie
escucha, y comprueba las dos mitades del contrato:

- sin la variable, la prueba se salta (lo comodo en local, con Docker apagado);
- con la variable, la misma prueba FALLA y el mensaje dice por que.

Se usa un subproceso y no `pytester` porque lo que se verifica es la fixture real de
`pruebas/conftest.py` con la configuracion real del repo, no una copia armada para la
prueba. No necesita Mongo: corre en la compuerta `-m "not mongo"`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# Una prueba marcada `mongo` que depende de la fixture `base_mongo` y de nada mas.
PRUEBA_OBJETIVO = (
    "pruebas/contrato/prueba_validadores_mongo.py::prueba_la_severidad_baja_no_existe"
)

# Puerto 1 en loopback: conexion rechazada al instante, nadie escucha ahi.
URI_SIN_NADIE = "mongodb://127.0.0.1:1/?directConnection=true"


def _correr(exigir: bool) -> subprocess.CompletedProcess[str]:
    entorno = {
        clave: valor
        for clave, valor in os.environ.items()
        if not clave.startswith("CENTINELA_")
    }
    entorno["CENTINELA_MONGO__URI"] = URI_SIN_NADIE
    entorno["CENTINELA_MONGO__TIEMPO_ESPERA_SELECCION_MS"] = "300"
    if exigir:
        entorno["CENTINELA_EXIGIR_MONGO"] = "1"

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            PRUEBA_OBJETIVO,
            "-q",
            "-rs",
            "-p",
            "no:cacheprovider",
        ],
        cwd=RAIZ,
        env=entorno,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def prueba_sin_la_variable_la_prueba_de_mongo_se_salta() -> None:
    resultado = _correr(exigir=False)
    salida = resultado.stdout + resultado.stderr

    assert resultado.returncode == 0, salida
    assert "1 skipped" in salida, salida
    assert "rsfraude no responde" in salida, salida


def prueba_con_la_variable_la_prueba_de_mongo_falla() -> None:
    resultado = _correr(exigir=True)
    salida = resultado.stdout + resultado.stderr

    assert resultado.returncode == 1, salida
    assert "skipped" not in salida, salida
    assert "error" in salida or "failed" in salida, salida
    assert "modo estricto" in salida, salida
