"""Contrato de configuracion: entorno > local > base, verificado, no a ojo.

Estas pruebas no leen `config/centinela.yml` del repo: escriben sus propios YAML en
un directorio temporal y mueven `CENTINELA_CONFIG_DIR`. Asi la prueba verifica el
mecanismo de precedencia y no los valores que hoy tenga el archivo del repo.

La ultima prueba si mira el archivo del repo, para sostener la regla de que en el
YAML no hay ni un numero de negocio.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.nucleo.config import (
    NOMBRE_YAML_BASE,
    NOMBRE_YAML_LOCAL,
    RUTA_RAIZ,
    VARIABLE_DIRECTORIO,
    Ajustes,
)

URI_DE_PRUEBA = "mongodb://localhost:27018/?replicaSet=rsfraude"

YAML_BASE = {
    "app": {
        "nombre": "Centinela",
        "entorno": "base",
        "pagina_por_omision": 10,
        "pagina_maxima": 100,
    },
    "mongo": {"uri": URI_DE_PRUEBA, "base": "antifraude_base"},
}


@pytest.fixture
def directorio_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Directorio de configuracion aislado, con solo el YAML base escrito.

    Empieza borrando toda variable `CENTINELA_*` del entorno. Es necesario y no es
    paranoia: quien tenga exportada `CENTINELA_MONGO__URI` en su terminal veria fallar
    estas pruebas sin entender por que, cuando lo que estarian detectando es su propio
    entorno ganandole al YAML, que es justo el comportamiento correcto.
    """
    import os

    for variable in list(os.environ):
        if variable.startswith("CENTINELA_"):
            monkeypatch.delenv(variable, raising=False)

    (tmp_path / NOMBRE_YAML_BASE).write_text(
        yaml.safe_dump(YAML_BASE), encoding="utf-8"
    )
    monkeypatch.setenv(VARIABLE_DIRECTORIO, str(tmp_path))
    return tmp_path


def escribir_local(directorio: Path, contenido: dict) -> None:
    (directorio / NOMBRE_YAML_LOCAL).write_text(
        yaml.safe_dump(contenido), encoding="utf-8"
    )


def prueba_el_yaml_base_se_lee_solo(directorio_yaml: Path) -> None:
    ajustes = Ajustes()

    assert ajustes.app.entorno == "base"
    assert ajustes.app.pagina_maxima == 100
    assert ajustes.mongo.base == "antifraude_base"


def prueba_el_local_le_gana_al_base(directorio_yaml: Path) -> None:
    escribir_local(directorio_yaml, {"app": {"pagina_maxima": 55}})

    ajustes = Ajustes()

    assert ajustes.app.pagina_maxima == 55, "el archivo local tiene que ganarle al base"


def prueba_el_entorno_le_gana_al_local(
    directorio_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    escribir_local(directorio_yaml, {"app": {"pagina_maxima": 55}})
    monkeypatch.setenv("CENTINELA_APP__PAGINA_MAXIMA", "77")

    ajustes = Ajustes()

    assert ajustes.app.pagina_maxima == 77, (
        "el entorno tiene que ganarle a los dos YAML"
    )


def prueba_los_tres_niveles_a_la_vez(
    directorio_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La cadena completa en una sola pasada, con un campo por nivel.

    Es la prueba que de verdad sostiene el contrato: cada valor tiene que venir del
    nivel que le corresponde, y el merge tiene que ser profundo (los campos que
    nadie sobrescribe sobreviven).
    """
    escribir_local(
        directorio_yaml,
        {"app": {"entorno": "local"}, "mongo": {"base": "antifraude_local"}},
    )
    monkeypatch.setenv("CENTINELA_MONGO__BASE", "antifraude_entorno")

    ajustes = Ajustes()

    assert ajustes.mongo.base == "antifraude_entorno"  # entorno gana
    assert ajustes.app.entorno == "local"  # local gana al base
    assert ajustes.app.nombre == "Centinela"  # nadie lo toco: sobrevive del base
    assert ajustes.app.pagina_por_omision == 10  # merge profundo, no reemplazo
    assert ajustes.mongo.uri == URI_DE_PRUEBA


def prueba_el_entorno_convierte_tipos(
    directorio_yaml: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una variable de entorno siempre llega como texto y tiene que quedar tipada."""
    monkeypatch.setenv("CENTINELA_APP__PAGINA_MAXIMA", "42")
    monkeypatch.setenv("CENTINELA_SERVIDOR__RECARGA", "false")

    ajustes = Ajustes()

    assert ajustes.app.pagina_maxima == 42
    assert isinstance(ajustes.app.pagina_maxima, int)
    assert ajustes.servidor.recarga is False


def prueba_una_clave_desconocida_en_el_yaml_no_arranca(directorio_yaml: Path) -> None:
    """Compuerta contra el numero de negocio de contrabando.

    Si alguien agrega `app.umbral_alerta` al YAML, la aplicacion no arranca: hay
    que declararlo en codigo, y al declararlo se ve que no pertenece ahi.
    """
    escribir_local(directorio_yaml, {"app": {"umbral_alerta": 70}})

    with pytest.raises(Exception) as fallo:
        Ajustes()

    assert "umbral_alerta" in str(fallo.value)


def prueba_sin_uri_no_arranca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`mongo.uri` y `mongo.base` no tienen valor por omision a proposito.

    Arrancar contra una URI adivinada es peor que no arrancar.
    """
    import os

    for variable in list(os.environ):
        if variable.startswith("CENTINELA_"):
            monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv(VARIABLE_DIRECTORIO, str(tmp_path))

    with pytest.raises(Exception) as fallo:
        Ajustes()

    assert "mongo" in str(fallo.value)


# ---------------------------------------------------------------------------
# El archivo real del repo
# ---------------------------------------------------------------------------

PALABRAS_DE_NEGOCIO = (
    "umbral",
    "peso",
    "puntaje",
    "severidad",
    "critica",
    "alta",
    "media",
    "regla",
    "corte",
    "monto",
    "fraude",
    "ventana_minutos",
)

PALABRAS_DE_SECRETO = ("password", "passwd", "secreto", "clave", "token", "apikey")


def prueba_el_yaml_del_repo_no_tiene_numeros_de_negocio() -> None:
    """Los cortes de severidad viven en Mongo (politica_deteccion), no aqui.

    Cambiar un umbral no debe requerir un despliegue; si el umbral esta en el YAML,
    si lo requiere.
    """
    ruta = RUTA_RAIZ / "config" / NOMBRE_YAML_BASE
    documento = yaml.safe_load(ruta.read_text(encoding="utf-8"))

    claves: list[str] = []

    def recorrer(nodo: object, camino: str = "") -> None:
        if isinstance(nodo, dict):
            for clave, valor in nodo.items():
                claves.append(f"{camino}.{clave}".lstrip("."))
                recorrer(valor, f"{camino}.{clave}")

    recorrer(documento)

    encontradas = [
        clave
        for clave in claves
        for palabra in PALABRAS_DE_NEGOCIO
        if palabra in clave.lower()
    ]
    assert not encontradas, (
        f"claves de negocio en {ruta.name}: {encontradas}. "
        "Los numeros de negocio van en la coleccion politica_deteccion de Mongo."
    )


def prueba_el_yaml_del_repo_no_tiene_secretos() -> None:
    texto = (RUTA_RAIZ / "config" / NOMBRE_YAML_BASE).read_text(encoding="utf-8").lower()
    # Se ignoran los comentarios: el archivo explica que los secretos no van aqui.
    cuerpo = "\n".join(
        linea for linea in texto.splitlines() if not linea.strip().startswith("#")
    )

    for palabra in PALABRAS_DE_SECRETO:
        assert palabra not in cuerpo, f"posible secreto en el YAML: {palabra}"

    assert not re.search(r"://[^/@\s]+:[^/@\s]+@", cuerpo), (
        "la URI del YAML no debe llevar usuario y contrasena"
    )


def prueba_el_yaml_local_no_esta_versionado() -> None:
    """El archivo local es de cada maquina; si se versiona, deja de servir."""
    ignorados = (RUTA_RAIZ / ".gitignore").read_text(encoding="utf-8")

    assert f"config/{NOMBRE_YAML_LOCAL}" in ignorados
    assert not (RUTA_RAIZ / "config" / NOMBRE_YAML_LOCAL).exists(), (
        "config/centinela.local.yml no se crea en el repo: es de cada maquina"
    )
