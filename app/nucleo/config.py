"""Configuracion de Centinela.

Este es el UNICO modulo del sistema que lee variables de entorno o archivos YAML.
Cualquier otro modulo recibe un objeto `Ajustes` ya armado. Si aparece un
`os.environ` o un `yaml.safe_load` en otra parte, es un error de capas.

Precedencia, de mayor a menor:

    entorno  >  config/centinela.local.yml  >  config/centinela.yml

La precedencia la verifica `pruebas/contrato/prueba_configuracion.py`, no el ojo.

El merge entre fuentes es profundo: `config/centinela.local.yml` puede traer solo
`app: {pagina_maxima: 25}` y el resto del bloque `app` se hereda del archivo base.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# app/nucleo/config.py -> app/nucleo -> app -> raiz del repo
RUTA_RAIZ = Path(__file__).resolve().parents[2]

NOMBRE_YAML_BASE = "centinela.yml"
NOMBRE_YAML_LOCAL = "centinela.local.yml"

# Variable reservada: mueve el directorio de configuracion completo. La usan las
# pruebas para apuntar a un tmp_path, y sirve para correr con otra configuracion
# sin tocar el repo. No es un ajuste del sistema, por eso no es un campo.
VARIABLE_DIRECTORIO = "CENTINELA_CONFIG_DIR"

PREFIJO_ENTORNO = "CENTINELA_"
SEPARADOR_ANIDADO = "__"


def directorio_config() -> Path:
    """Directorio donde viven los dos YAML."""
    crudo = os.environ.get(VARIABLE_DIRECTORIO)
    if crudo:
        return Path(crudo).expanduser()
    return RUTA_RAIZ / "config"


class AjustesApp(BaseModel):
    """Identidad del proceso y guardas tecnicas de la API.

    `extra="forbid"` a proposito: si alguien agrega una clave al YAML que no esta
    declarada aqui, la aplicacion no arranca. Es la compuerta que impide que un
    numero de negocio entre de contrabando en la configuracion.
    """

    model_config = ConfigDict(extra="forbid")

    nombre: str = "Centinela"
    entorno: str = "dev"
    pagina_por_omision: int = Field(default=50, ge=1)
    pagina_maxima: int = Field(default=200, ge=1)

    @model_validator(mode="after")
    def omision_no_pasa_el_tope(self) -> AjustesApp:
        if self.pagina_por_omision > self.pagina_maxima:
            raise ValueError(
                "app.pagina_por_omision no puede ser mayor que app.pagina_maxima"
            )
        return self


class AjustesServidor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    puerto: int = Field(default=8000, ge=1, le=65535)
    recarga: bool = True


class AjustesMongo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    base: str
    tiempo_espera_seleccion_ms: int = Field(default=5000, ge=100)


class AjustesSupervision(BaseModel):
    """Tiempos de la vigilancia de tareas de fondo que publica `/estado`.

    Son numeros tecnicos, no de negocio: no deciden si algo es fraude, deciden cada
    cuanto el proceso se toma el pulso y cuanto silencio se tolera antes de declarar
    una tarea retrasada. Por eso pueden vivir en el YAML.

    La tolerancia tiene que ser varias veces el intervalo. Si fueran parecidos, un
    latido que llega un instante tarde dejaria `/estado` en `degradado` sin que nada
    este mal, y una alarma que grita sin motivo se aprende a ignorar.
    """

    model_config = ConfigDict(extra="forbid")

    intervalo_latido_segundos: float = Field(default=5.0, gt=0)
    tolerancia_retraso_segundos: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def la_tolerancia_supera_el_intervalo(self) -> AjustesSupervision:
        if self.tolerancia_retraso_segundos <= self.intervalo_latido_segundos:
            raise ValueError(
                "supervision.tolerancia_retraso_segundos tiene que ser mayor que "
                "supervision.intervalo_latido_segundos"
            )
        return self


class AjustesCanal(BaseModel):
    """El canal SSE de alertas. Tambien tecnico: ni un umbral, ni una severidad."""

    model_config = ConfigDict(extra="forbid")

    # Cada cuanto se manda un comentario de mantenimiento por la conexion abierta. Sin
    # esto, un proxy o un cortafuegos cierra una conexion sin trafico y el panel se
    # queda en silencio pareciendo sano.
    ping_segundos: int = Field(default=15, ge=1)
    # Cuanto espera el navegador antes de reconectar. Se manda como `retry` en la
    # apertura para no depender del valor por omision de cada navegador.
    reintento_ms: int = Field(default=3000, ge=100)


class AjustesRegistro(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nivel: str = "INFO"


class FuenteYaml(PydanticBaseSettingsSource):
    """Lee un YAML. Si el archivo no existe, aporta nada (no falla).

    El archivo local es opcional por diseno: cada quien lo crea si necesita
    cambiar algo en su maquina, y no se versiona.
    """

    def __init__(self, settings_cls: type[BaseSettings], ruta: Path) -> None:
        super().__init__(settings_cls)
        self.ruta = ruta

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        # No se usa: esta fuente entrega el documento completo en __call__.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if not self.ruta.is_file():
            return {}
        contenido = yaml.safe_load(self.ruta.read_text(encoding="utf-8"))
        if contenido is None:
            return {}
        if not isinstance(contenido, dict):
            raise TypeError(f"{self.ruta} debe contener un mapa en la raiz")
        return contenido

    def __repr__(self) -> str:
        return f"FuenteYaml({self.ruta})"


class Ajustes(BaseSettings):
    """Configuracion completa del proceso."""

    # extra="ignore" en el nivel de arriba porque el prefijo CENTINELA_ tambien
    # captura variables que no son campos (CENTINELA_CONFIG_DIR, por ejemplo).
    model_config = SettingsConfigDict(
        env_prefix=PREFIJO_ENTORNO,
        env_nested_delimiter=SEPARADOR_ANIDADO,
        extra="ignore",
    )

    app: AjustesApp = AjustesApp()
    servidor: AjustesServidor = AjustesServidor()
    mongo: AjustesMongo
    supervision: AjustesSupervision = AjustesSupervision()
    canal: AjustesCanal = AjustesCanal()
    registro: AjustesRegistro = AjustesRegistro()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """El orden ES la precedencia: la primera fuente gana.

        El directorio se resuelve en cada llamada, no al importar el modulo, para
        que una prueba pueda moverlo con monkeypatch.
        """
        directorio = directorio_config()
        return (
            init_settings,
            env_settings,
            FuenteYaml(settings_cls, directorio / NOMBRE_YAML_LOCAL),
            FuenteYaml(settings_cls, directorio / NOMBRE_YAML_BASE),
        )


@lru_cache(maxsize=1)
def obtener_ajustes() -> Ajustes:
    """Ajustes del proceso, leidos una sola vez.

    Se usa como dependencia de FastAPI y desde el lifespan. Las pruebas que
    cambian el entorno tienen que llamar `obtener_ajustes.cache_clear()`.
    """
    return Ajustes()
