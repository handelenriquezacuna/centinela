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
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

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
    # Tope de alertas por peticion de transicion en lote (T-56): guarda tecnica.
    lote_maximo: int = Field(default=50, ge=1)

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


class AjustesDeteccion(BaseModel):
    """Tiempos del disparador D1 (H-09). Tecnicos: ninguno decide si algo es fraude.

    - `espera_cursor_ms`: cuanto espera el servidor una transaccion nueva antes de
      devolver la vuelta vacia. Cada vuelta, con o sin evento, es un latido de D1 en
      `/estado`; por eso tiene que ser bastante menor que la tolerancia de retraso, o
      un D1 sano sin trafico apareceria como `retrasada`.
    - `reintentos`: cuantas veces se reintenta un evento que fallo antes de dejarlo
      en el descarte (`deteccion_estado`, clase `descarte`).
    - `espera_inicial_segundos` / `espera_maxima_segundos`: la espera progresiva entre
      reintentos, y entre reaperturas del change stream si se cae. Se duplica en cada
      intento hasta el tope.
    - `lote_maximo` (T-62): hasta cuantos eventos del change stream procesa D1 por lote.
      Toma lo que haya, sin esperar a llenarlo. 1 es el camino por evento de siempre.
    - `rezago_intervalo_segundos`: cada cuanto D1 mide cuantas transacciones tiene sin
      evaluar (T-57). Es una consulta barata por indice, pero no se hace por evento.
    - `rezago_aviso_desde`: desde cuantas transacciones sin evaluar el panel muestra el
      aviso "Deteccion atrasada". Es operacion, no una regla de negocio.
    - `rezago_aviso_atraso_segundos`: desde cuantos segundos de atraso (ahora menos la
      insercion del ultimo evento que D1 termino) el panel muestra el mismo aviso, aunque
      el conteo de pendientes diga poco. No depende del orden de los ids.
    """

    model_config = ConfigDict(extra="forbid")

    espera_cursor_ms: int = Field(default=1000, ge=10)
    reintentos: int = Field(default=3, ge=0)
    espera_inicial_segundos: float = Field(default=0.5, gt=0)
    espera_maxima_segundos: float = Field(default=10.0, gt=0)
    lote_maximo: int = Field(default=200, ge=1, le=5000)
    rezago_intervalo_segundos: float = Field(default=2.0, gt=0)
    rezago_aviso_desde: int = Field(default=500, ge=1)
    rezago_aviso_atraso_segundos: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def el_tope_supera_la_espera_inicial(self) -> AjustesDeteccion:
        if self.espera_maxima_segundos < self.espera_inicial_segundos:
            raise ValueError(
                "deteccion.espera_maxima_segundos no puede ser menor que "
                "deteccion.espera_inicial_segundos"
            )
        return self


class AjustesIndicadores(BaseModel):
    """Disparador D3 (H-22): cada cuanto se recalcula el indicador del dia actual.

    Tecnico, no de negocio. D3 late una vez por recalculo, asi que el intervalo tiene
    que ser menor que `supervision.tolerancia_retraso_segundos` (lo exige `Ajustes`).
    """

    model_config = ConfigDict(extra="forbid")

    intervalo_segundos: float = Field(default=10.0, gt=0)


class AjustesRasgosGrafo(BaseModel):
    """Rasgos de grafo por cuenta (T-63): la extension de D3 que alimenta a R-11.

    Tecnico, no de negocio: no dice que es sospechoso (eso es el umbral y los parametros de
    la regla, en Mongo), dice cuanta historia mira el calculo y cuanto se da por bueno
    un rasgo despues de calculado.

    - `horizonte_horas`: cuanto hacia atras desde cada recalculo se miran las
      transferencias. Acota el trabajo del pipeline: el indice por fecha hace que
      solo se lea ese rango.
    - `vigencia_segundos`: pasado este tiempo desde su corte, un rasgo ya no vale y R-11
      queda como no evaluada. Tiene que ser mayor que `indicadores.intervalo_segundos`
      (D3 recalcula con ese ritmo), o un D3 sano dejaria rasgos vencidos.
    """

    model_config = ConfigDict(extra="forbid")

    horizonte_horas: float = Field(default=24.0, gt=0)
    vigencia_segundos: int = Field(default=60, ge=1)


class AjustesPerfiles(BaseModel):
    """Recalculo automatico de `perfiles_comportamiento` (H-21, T-76). Tecnico, no de negocio.

    Los perfiles los leen R-01 y R-05, asi que recalcularlos cambia la deteccion: por eso
    `activo` es false por omision y el recalculo es una decision, no un efecto de arrancar
    la app. A mano siempre se puede: `python tareas.py perfiles`.

    - `intervalo_segundos`: cada cuanto lo repite D3 cuando esta activo. Un perfil mira 90
      dias; en una hora cambia en menos del 0,05 % de su historia, asi que mas seguido no
      mueve el resultado y si cuesta: el pipeline lee todo el rango de `transacciones` y hace
      dos `$lookup` por transaccion. Por eso no usa el ritmo de 10 segundos de los indicadores.
    - `dias`: cuantos dias hacia atras mira el perfil.
    """

    model_config = ConfigDict(extra="forbid")

    activo: bool = False
    intervalo_segundos: int = Field(default=3600, ge=60)
    dias: int = Field(default=90, ge=1, le=365)


class AjustesSesion(BaseModel):
    """Sesion del portal y la API (H-25). Tecnico: duracion y forma de la cookie.

    `clave` firma los tokens y es un SECRETO: no va en el YAML, entra por la variable
    `CENTINELA_SESION__CLAVE`. Sin ella, en entorno `dev` el proceso genera una aleatoria
    al arrancar (las sesiones no sobreviven a un reinicio) y `/estado` lo advierte; en
    cualquier otro entorno la aplicacion no arranca.
    """

    model_config = ConfigDict(extra="forbid")

    minutos: int = Field(default=60, ge=1, le=24 * 60)
    # Secure: la cookie solo viaja por HTTPS. Apagado por omision porque el portal local
    # corre por HTTP; se enciende detras de TLS.
    cookie_segura: bool = False
    nombre_cookie: str = Field(default="centinela_sesion", min_length=1)
    clave: str | None = Field(default=None, min_length=32, repr=False)


class AjustesPortal(BaseModel):
    """Que interfaz sirven las rutas de pagina (T-61, T-67, D-60 a D-63). Tecnico.

    - `spa` (por omision desde T-67): `/`, `/panel`, `/detalle/...` y las demas devuelven
      el `index.html` de la SPA React construida en `app/estaticos/spa/`
      (`python tareas.py front`). Las rutas HTMX que no son de pagina (fragmentos,
      formularios) siguen registradas mientras viva el respaldo.
    - `htmx`: las paginas Jinja + HTMX. Respaldo hasta T-68 (2 de diciembre), que lo
      retira. Se vuelve a el con `CENTINELA_PORTAL__INTERFAZ=htmx`. Ver docs/06-plataforma.md.
    """

    model_config = ConfigDict(extra="forbid")

    interfaz: Literal["htmx", "spa"] = "spa"


class AjustesRegistro(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nivel: str = "INFO"


class EntidadSinpe(BaseModel):
    """Entidad financiera y el tope de SINPE Movil que aplica a sus cuentas.

    En Costa Rica el tope por transferencia depende del banco (de 100.000 a 300.000
    colones). Es un dato de la simulacion, no una regla de deteccion.
    """

    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1)
    tope_crc: int = Field(ge=100_000, le=300_000)


class AjustesGenerador(BaseModel):
    """Parametros del generador de datos sinteticos (`python -m app.generador`).

    Son parametros de simulacion: no son reglas de deteccion (esas viven en Mongo).
    Cambiar la lista de entidades o sus topes cambia lo que se genera sin tocar codigo.
    """

    model_config = ConfigDict(extra="forbid")

    semilla: int = 20261207
    clientes: int = Field(default=200, ge=10)
    transacciones: int = Field(default=5000, ge=100)
    # Transacciones por segundo al insertar. 0 = sin limite (lo mas rapido posible).
    ritmo_por_segundo: int = Field(default=0, ge=0)
    # Fraccion del volumen total que se inyecta como fraude; estrictamente menor a 2 %.
    proporcion_anomalos: float = Field(default=0.012, ge=0, lt=0.02)
    # Fecha (UTC) en que termina la historia generada; fija para que sea reproducible.
    fecha_fin: date = date(2026, 11, 22)
    dias_historia: int = Field(default=30, ge=1, le=365)
    tamano_lote: int = Field(default=1000, ge=1, le=10_000)
    # Fraccion de las cuentas mula de las cadenas que se reporta en `listas_riesgo`
    # (vigente, con fecha de reporte anterior a su primera transaccion recibida).
    proporcion_mulas_reportadas: float = Field(default=0.5, ge=0, le=1)
    entidades: list[EntidadSinpe] = Field(
        default_factory=lambda: [
            EntidadSinpe(nombre="Entidad A", tope_crc=300_000),
            EntidadSinpe(nombre="Entidad B", tope_crc=200_000),
            EntidadSinpe(nombre="Entidad C", tope_crc=150_000),
            EntidadSinpe(nombre="Entidad D", tope_crc=100_000),
        ],
        min_length=4,
    )

    @model_validator(mode="after")
    def las_entidades_no_se_repiten(self) -> AjustesGenerador:
        nombres = [entidad.nombre for entidad in self.entidades]
        if len(set(nombres)) != len(nombres):
            raise ValueError("generador.entidades no puede repetir nombres")
        return self


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
    deteccion: AjustesDeteccion = AjustesDeteccion()
    indicadores: AjustesIndicadores = AjustesIndicadores()
    rasgos_grafo: AjustesRasgosGrafo = AjustesRasgosGrafo()
    perfiles: AjustesPerfiles = AjustesPerfiles()
    sesion: AjustesSesion = AjustesSesion()
    portal: AjustesPortal = AjustesPortal()
    registro: AjustesRegistro = AjustesRegistro()
    generador: AjustesGenerador = Field(default_factory=AjustesGenerador)

    @model_validator(mode="after")
    def los_rasgos_duran_mas_que_el_intervalo_de_d3(self) -> Ajustes:
        """D3 recalcula los rasgos con el ritmo de `indicadores`; si la vigencia fuera
        menor, un D3 sano los dejaria vencidos entre un recalculo y el siguiente."""
        if self.rasgos_grafo.vigencia_segundos <= self.indicadores.intervalo_segundos:
            raise ValueError(
                "rasgos_grafo.vigencia_segundos tiene que ser mayor que "
                "indicadores.intervalo_segundos"
            )
        return self

    @model_validator(mode="after")
    def el_cursor_late_antes_de_la_tolerancia(self) -> Ajustes:
        """D1 late una vez por vuelta del cursor; si la vuelta dura mas que la
        tolerancia, `/estado` declararia retrasado a un D1 sano y sin trafico."""
        espera = self.deteccion.espera_cursor_ms / 1000
        if espera >= self.supervision.tolerancia_retraso_segundos:
            raise ValueError(
                "deteccion.espera_cursor_ms tiene que ser menor que "
                "supervision.tolerancia_retraso_segundos"
            )
        return self

    @model_validator(mode="after")
    def la_sesion_tiene_clave_fuera_de_dev(self) -> Ajustes:
        """Solo en `dev` se tolera arrancar sin clave de firma (se genera una al vuelo)."""
        if self.app.entorno != "dev" and self.sesion.clave is None:
            raise ValueError(
                "falta la clave de firma de sesion: definir CENTINELA_SESION__CLAVE "
                "(obligatoria cuando app.entorno no es dev)"
            )
        return self

    @model_validator(mode="after")
    def d3_late_antes_de_la_tolerancia(self) -> Ajustes:
        """D3 late una vez por recalculo (mismo razonamiento que D1)."""
        if self.indicadores.intervalo_segundos >= self.supervision.tolerancia_retraso_segundos:
            raise ValueError(
                "indicadores.intervalo_segundos tiene que ser menor que "
                "supervision.tolerancia_retraso_segundos"
            )
        return self

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
