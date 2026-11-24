"""Respuestas JSON de las lecturas que una pantalla del portal necesita (T-61).

La SPA React no calcula: pide estas respuestas y las dibuja. Cada una es la misma
informacion que la pagina HTMX equivalente arma en el servidor, sacada de las mismas
funciones puras (`app/api/detalle.py`, `app/api/casos.py`, `app/api/sankey.py`,
`app/api/graficos.py`). Las reglas de siempre valen aqui:

- dinero en colones enteros (`*_crc: int`), nunca flotante;
- fechas en UTC con zona; la SPA las presenta en hora de Costa Rica;
- identificadores legibles; las filas de alerta publican `_id` igual que `Alerta`;
- la etiqueta oculta `simulacion` del generador nunca sale por aqui.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.api.esquemas import Pagina
from app.modelos.alertas import Alerta, RolAgente
from app.modelos.base import MarcaTiempoUTC, ModeloCentinela
from app.modelos.estados import EstadoAlerta, Severidad
from app.modelos.transaccional import EntradaListaRiesgo
from app.repos.red_mulas import RedMulas

# ---------------------------------------------------------------------------
# Sesion
# ---------------------------------------------------------------------------


class PaginaPermitida(BaseModel):
    """Una pantalla de la navegacion que el rol de la sesion puede abrir."""

    ruta: str = Field(examples=["/panel"])
    rotulo: str = Field(examples=["Panel"])


class SesionActual(BaseModel):
    """Quien es la sesion de la cookie (o del Bearer) y que puede hacer.

    La SPA no guarda el token: pregunta esto al cargar. `permisos` sale de la tabla
    unica de `app/seguridad/permisos.py`; la SPA lo usa para mostrar u ocultar, nunca
    para decidir (decide el guardia del servidor).
    """

    agente_id: str = Field(examples=["AGT-001"])
    rol: RolAgente
    expira: MarcaTiempoUTC
    permisos: list[str] = Field(examples=[["sesion", "panel", "detalle"]])
    navegacion: list[PaginaPermitida]
    paginas: list[str] = Field(
        description=(
            "Plantillas de las rutas de pagina que el rol puede abrir, tal como las declara "
            "la tabla de permisos (`/detalle/{alerta_id}`). La SPA guarda cada pantalla con "
            "esta lista en vez de copiar la tabla"
        ),
        examples=[["/", "/login", "/panel", "/detalle/{alerta_id}"]],
    )


# ---------------------------------------------------------------------------
# Detalle de alerta (H-18)
# ---------------------------------------------------------------------------


class ReglaExplicada(BaseModel):
    """Una regla disparada (v2) con su aporte, el acumulado y la evidencia calculada."""

    regla_id: str = Field(examples=["REG-001"])
    codigo: str = Field(examples=["R-01"])
    version: int = Field(ge=1)
    peso: int = Field(ge=0, description="Aporte al puntaje, antes del tope de la politica")
    explicacion: str = Field(description="Sin el prefijo 'R-01 v2:' ni '(aporta 35)'")
    evidencia: str | None = Field(default=None, description="Linea calculada con el perfil")
    acumulado: int = Field(ge=0, description="Suma de los aportes hasta esta regla")
    ancho: int = Field(ge=0, le=100, description="Porcentaje del puntaje maximo, para la barra")


class CorteExplicado(BaseModel):
    severidad: str = Field(examples=["CRITICA"])
    minimo: int
    maximo: int
    inicio: int = Field(ge=0, le=100, description="Porcentaje del puntaje maximo")
    alcanzado: bool = Field(description="El puntaje de la alerta cae en este tramo")


class ExplicacionPuntaje(BaseModel):
    filas: list[ReglaExplicada]
    suma: int = Field(description="Suma de aportes; puede pasar del puntaje si hubo tope")
    tope: bool = Field(description="La suma supero el puntaje: la politica lo acoto")
    maximo: int
    cortes: list[CorteExplicado]
    ancho_puntaje: int = Field(ge=0, le=100)
    umbral: int | None = None


class AccionDisponible(BaseModel):
    """Una transicion que la maquina de estados permite desde el estado actual."""

    destino: EstadoAlerta
    rotulo: str = Field(examples=["Confirmar fraude"])
    ayuda: str
    cierra: bool = Field(description="La accion cierra la alerta: la interfaz pide confirmar")


class TransaccionDetalle(BaseModel):
    """La transaccion de la alerta. No expone la etiqueta oculta `simulacion`."""

    transaccion_id: str = Field(examples=["TXN-001"])
    cuenta_origen: str = Field(examples=["CTA-001"])
    sinpe_origen: str | None = None
    cuenta_destino: str
    monto_crc: int
    moneda: str = "CRC"
    canal: str | None = None
    fecha: MarcaTiempoUTC


class ClienteDetalle(BaseModel):
    cliente_id: str = Field(examples=["CLI-001"])
    nombre: str
    cedula: str | None = None


class HorarioDetalle(BaseModel):
    inicio: str | None = Field(default=None, examples=["07:00"])
    fin: str | None = Field(default=None, examples=["20:00"])


class PerfilDetalle(BaseModel):
    promedio_crc: int | None = None
    maximo_crc: int | None = None
    num_transferencias: int | None = None
    horario_habitual: HorarioDetalle | None = None
    destinos_frecuentes: list[str] = Field(default_factory=list)


class PoliticaDetalle(BaseModel):
    """De que politica salen los cortes que se muestran.

    `origen`: `usada` (la que la alerta registra), `vigente` (la alerta no registra una o
    su version ya no esta; los cortes son los de ahora) o `ninguna` (no hay politica).
    """

    politica_id: str | None = Field(default=None, examples=["POL-001"])
    version: int | None = None
    origen: Literal["usada", "vigente", "ninguna"]


class DetalleAlerta(BaseModel):
    """Todo lo que la pantalla de detalle muestra de una alerta, ya explicado."""

    alerta: Alerta
    es_v2: bool = Field(description="Falso en una alerta v1: no trae aporte por regla")
    aviso_v1: str | None = Field(default=None, description="Que hacer con una alerta v1")
    transaccion: TransaccionDetalle | None = None
    cliente: ClienteDetalle | None = None
    perfil: PerfilDetalle | None = None
    politica: PoliticaDetalle
    puntaje: ExplicacionPuntaje
    acciones: list[AccionDisponible]
    caso_id: str | None = Field(default=None, description="CAS-xxx si la alerta se escalo")
    nombres_agentes: dict[str, str] = Field(
        default_factory=dict, description="agente_id -> nombre, para leer el historial"
    )


# ---------------------------------------------------------------------------
# Panel por cuenta destino (T-56)
# ---------------------------------------------------------------------------


class AlertaDeCaso(ModeloCentinela):
    """Fila de una alerta dentro de su cuenta destino. Publica `_id`, como `Alerta`."""

    alerta_id: str = Field(alias="_id", examples=["ALR-001"])
    estado: EstadoAlerta
    severidad: Severidad
    puntaje: int
    cliente_nombre: str
    cuenta_origen: str
    cuenta_destino: str
    monto_crc: int
    fecha_creacion: MarcaTiempoUTC
    agente_id: str | None = None


class ReglaRepetida(BaseModel):
    codigo: str = Field(examples=["R-01"])
    veces: int = Field(ge=1)


class AccionLote(BaseModel):
    """Una accion que la maquina permite para al menos una alerta de la cuenta."""

    destino: EstadoAlerta
    rotulo: str


class CasoDestinoRespuesta(BaseModel):
    """Alertas abiertas hacia una misma cuenta destino. No es la coleccion `casos`."""

    model_config = ConfigDict(populate_by_name=True)

    cuenta_destino: str = Field(examples=["6033-9001"])
    total_alertas: int = Field(ge=0)
    victimas: int = Field(ge=0)
    monto_total_crc: int = Field(ge=0, description="Colones enteros")
    severidad_max: Severidad
    puntaje_max: int
    mas_antigua: MarcaTiempoUTC
    mas_reciente: MarcaTiempoUTC
    reglas: list[ReglaRepetida]
    alertas: list[AlertaDeCaso]
    alertas_ocultas: int = Field(ge=0, description="Alertas de la cuenta que no vienen en la lista")
    en_lista_riesgo: bool
    sugerencia: str | None = Field(default=None, description="Ayuda; decide el agente")
    acciones: list[AccionLote]


class PaginaCasosDestino(Pagina):
    elementos: list[CasoDestinoRespuesta]


class AvisoRezago(BaseModel):
    """El aviso de D1 atrasado (T-57). Lee la ultima medicion; no consulta la base."""

    atrasada: bool = Field(description="Pendientes por encima de deteccion.rezago_aviso_desde")
    pendientes: int | None = Field(default=None, description="Nulo sin medicion")
    aviso_desde: int = Field(ge=0)
    segundos_para_ponerse_al_dia: float | None = None
    estimado: str = Field(default="", description="El estimado en palabras: '2 min'")
    medido_en: MarcaTiempoUTC | None = None


# ---------------------------------------------------------------------------
# Tablero (H-20)
# ---------------------------------------------------------------------------


class RespuestaTablero(BaseModel):
    """El rollup del periodo y la geometria de sus graficos, ya calculada.

    `resumen` es la salida de `app/repos/tablero.resumir` tal cual (con `vacio: true` si
    no hay `indicadores_diarios`); `graficos` es la de `app/api/graficos.graficos_tablero`.
    Se publican como objetos abiertos en esta fase: la fase 2 los tipa al migrar la
    pantalla.
    """

    dias: int = Field(ge=1)
    periodos: list[int]
    comando_recalculo: str = Field(examples=["python tareas.py indicadores"])
    resumen: dict[str, Any]
    graficos: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Sankey y grafo de la red de mulas (T-55, T-35)
# ---------------------------------------------------------------------------


class NodoSankey(BaseModel):
    x: int
    y: int
    ancho: int
    alto: int
    cuenta: str
    columna: int
    externa: bool
    en_lista: bool
    inicio: bool
    victima: bool
    nombre: str | None = None


class BandaSankey(BaseModel):
    d: str = Field(description="Trazado SVG de la banda, en pixeles enteros")
    grosor: int = Field(ge=0, description="Proporcional a los colones, entero")
    tx: int
    ty: int
    salto: int
    origen: str
    destino: str
    monto_crc: int
    fecha: MarcaTiempoUTC
    transaccion_id: str


class CuentaSankey(BaseModel):
    victima: bool
    nombre: str | None = None
    cuenta: str
    cuenta_id: str | None = None
    externa: bool
    en_lista: bool
    columna: int


class DisposicionSankey(BaseModel):
    ancho: int
    alto: int
    columnas: int
    saltos: int
    transferencias: int
    resumen: str
    nodos: list[NodoSankey]
    bandas: list[BandaSankey]
    cuentas: list[CuentaSankey]


class RespuestaSankey(BaseModel):
    """`sankey` es nulo si la cuenta no tiene salidas en la ventana; `motivo` dice por que."""

    cuenta: str
    sankey: DisposicionSankey | None = None
    motivo: str | None = None


class NodoGrafo(BaseModel):
    x: int
    y: int
    ancho: int
    alto: int
    cuenta: str
    cuenta_id: str | None = None
    externa: bool
    en_lista: bool
    inicio: bool


class AristaGrafo(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    tx: int
    ty: int
    monto_crc: int
    minutos: float | None = None
    salto: int


class DisposicionGrafo(BaseModel):
    ancho: int
    alto: int
    nodos: list[NodoGrafo]
    aristas: list[AristaGrafo]


class RespuestaGrafo(BaseModel):
    """La red, su disposicion y, para quien puede dar de baja, las entradas vigentes."""

    red: RedMulas
    grafo: DisposicionGrafo | None = Field(default=None, description="Nulo si no hay aristas")
    entradas_lista: list[EntradaListaRiesgo] = Field(
        default_factory=list,
        description="Entradas de listas_riesgo de las cuentas de la red; solo con permiso",
    )
