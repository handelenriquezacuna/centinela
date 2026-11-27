/*
 * Tipos de las respuestas de la API que usa la SPA. Espejo de los esquemas de
 * app/api/esquemas.py y app/api/esquemas_portal.py (publicados en /openapi.json).
 * Dinero: `number` que SIEMPRE es entero de colones; fechas: texto ISO en UTC.
 */

export type EstadoAlerta = "nueva" | "en_revision" | "confirmada" | "descartada" | "escalada";
export type Severidad = "MEDIA" | "ALTA" | "CRITICA";
export type Rol = "agente" | "supervisor" | "administrador";

/** La forma unica del error de la API. */
export interface CuerpoError {
  error: {
    codigo: string;
    mensaje: string;
    detalles: { campo: string | null; mensaje: string }[];
  };
}

export interface PaginaPermitida {
  ruta: string;
  rotulo: string;
  /** Grupo del menu: Alertas, Supervisión o Administración. */
  area: string;
}

export interface SesionActual {
  agente_id: string;
  /** Nulo si el agente ya no existe: se muestra el identificador. */
  nombre: string | null;
  rol: Rol;
  /** Pagina donde aterriza el rol al entrar. */
  inicio: string;
  expira: string;
  permisos: string[];
  navegacion: PaginaPermitida[];
  /** Plantillas de pagina que el rol puede abrir: `/detalle/{alerta_id}`. */
  paginas: string[];
}

export interface Alerta {
  _id: string;
  esquema_version: 1 | 2;
  transaccion_id: string;
  puntaje: number;
  severidad: Severidad;
  estado: EstadoAlerta;
  agente_id: string | null;
  fecha_creacion: string;
  cuenta_origen: string;
  cuenta_destino: string;
  monto_crc: number;
  moneda: "CRC";
  cliente_nombre: string;
  reglas_disparadas: unknown[];
  historial: unknown[];
}

export interface AlertaDeCaso {
  _id: string;
  estado: EstadoAlerta;
  severidad: Severidad;
  puntaje: number;
  cliente_nombre: string;
  cuenta_origen: string;
  cuenta_destino: string;
  monto_crc: number;
  fecha_creacion: string;
  agente_id: string | null;
}

export interface AccionLote {
  destino: EstadoAlerta;
  rotulo: string;
}

export interface CasoDestino {
  cuenta_destino: string;
  total_alertas: number;
  victimas: number;
  monto_total_crc: number;
  severidad_max: Severidad;
  puntaje_max: number;
  mas_antigua: string;
  mas_reciente: string;
  reglas: { codigo: string; veces: number }[];
  alertas: AlertaDeCaso[];
  alertas_ocultas: number;
  en_lista_riesgo: boolean;
  sugerencia: string | null;
  acciones: AccionLote[];
}

export interface Pagina<T> {
  total: number;
  limite: number;
  desplazamiento: number;
  elementos: T[];
}

export interface AvisoRezago {
  /** Pendientes sobre `aviso_desde` o atraso sobre `aviso_atraso_desde`. */
  atrasada: boolean;
  /** La causa es el conteo de pendientes; si es falso, solo el atraso en segundos. */
  por_conteo: boolean;
  pendientes: number | null;
  aviso_desde: number;
  atraso_segundos: number | null;
  aviso_atraso_desde: number;
  atraso_legible: string;
  segundos_para_ponerse_al_dia: number | null;
  estimado: string;
  medido_en: string | null;
}

export interface ResultadoItemLote {
  alerta_id: string;
  aplicada: boolean;
  estado_http: number;
  codigo: string | null;
  mensaje: string;
  estado_actual: string | null;
  caso_id: string | null;
}

export interface ResultadoLote {
  solicitadas: number;
  aplicadas: number;
  rechazadas: number;
  resultados: ResultadoItemLote[];
}

/** Cuerpo del evento `alerta` de GET /api/v1/alertas/eventos (formato 1). */
export interface EventoAlerta {
  formato: 1;
  operacion: "insert" | "update" | "replace";
  alerta: Alerta;
}

/** Cuerpo del evento `resincronizar` (formato 1). */
export interface EventoResincronizar {
  formato: 1;
  motivo: string;
}

/** Una transaccion del historico con su alerta asociada (GET /api/v1/transacciones). */
export interface ElementoTransaccion {
  transaccion_id: string;
  cuenta_origen: string;
  sinpe_origen: string | null;
  cuenta_destino: string;
  monto_crc: number;
  moneda: string;
  canal: string;
  fecha: string;
  alerta_id: string | null;
  alerta_estado: EstadoAlerta | null;
}

/* ---------- Tablero (GET /api/v1/tablero, GET /api/v1/efectividad) ---------- */

export interface VariacionConteo {
  actual: number;
  previo: number;
  delta: number;
  porcentaje: number | null;
}

/** Un indicador del periodo contra el anterior; sin dato en un lado, `delta` y `porcentaje` son nulos. */
export interface Comparacion {
  actual: number | null;
  previo: number | null;
  delta: number | null;
  porcentaje: number | null;
}

export interface KpiPeriodo {
  disponible: boolean;
  tiempo_decision_s: number | null;
  monto_protegido_crc: number | null;
  tasa_confirmacion: number | null;
  pendientes: number | null;
  pendiente_mas_antigua: string | null;
}

export interface TotalesPeriodo {
  total: number;
  por_severidad: Record<string, number>;
  por_estado: Record<string, number>;
  por_agente: Record<string, number> | null;
  por_hora: Record<string, number> | null;
  dias_con_datos: number;
  kpi: KpiPeriodo;
}

export interface KpiTablero {
  disponible: boolean;
  tiempo_decision_s: Comparacion;
  monto_protegido_crc: Comparacion;
  tasa_confirmacion: Comparacion;
  pendientes: Comparacion;
  pendiente_mas_antigua: string | null;
  antiguedad_horas: number | null;
}

export interface RangoFechas {
  desde: string;
  hasta: string;
}

export interface ResumenVacio {
  vacio: true;
  dias: number;
}

export interface ResumenTablero {
  vacio: false;
  dias: number;
  periodo_actual: RangoFechas;
  periodo_previo: RangoFechas;
  calculado_en: string | null;
  actual: TotalesPeriodo;
  previo: TotalesPeriodo;
  total: VariacionConteo;
  severidad: Record<string, VariacionConteo>;
  estado: Record<string, VariacionConteo>;
  hay_periodo_previo: boolean;
  kpi: KpiTablero;
}

export interface FilaBarra {
  y: number;
  rotulo: string;
  valor: number;
  clase: string;
  ancho: number;
}

export interface GraficoBarras {
  ancho: number;
  alto: number;
  etiqueta: number;
  filas: FilaBarra[];
}

export interface ColumnaHora {
  x: number;
  y: number;
  alto: number;
  hora: string;
  valor: number;
}

export interface GraficoColumnas {
  ancho: number;
  alto: number;
  base: number;
  paso: number;
  columnas: ColumnaHora[];
}

export interface GraficosTablero {
  estado: GraficoBarras;
  severidad: GraficoBarras;
  agente: GraficoBarras | null;
  hora: GraficoColumnas | null;
}

export interface RespuestaTablero {
  dias: number;
  periodos: number[];
  comando_recalculo: string;
  resumen: ResumenVacio | ResumenTablero;
  graficos: GraficosTablero | null;
}

export interface MetricasPatron {
  patron: string;
  fraudes: number;
  detectados: number;
  perdidos: number;
  exhaustividad: number | null;
  episodios: number;
  episodios_detectados: number;
}

export interface ReporteEfectividad {
  transacciones_etiquetadas: number;
  fraudes: number;
  legitimas: number;
  detectados: number;
  perdidos: number;
  falsos_positivos: number;
  verdaderos_negativos: number;
  alertas_totales: number;
  alertas_sin_etiqueta: number;
  precision: number | null;
  exhaustividad: number | null;
  por_patron: MetricasPatron[];
}

/* ---------- Reglas y politica (/api/v1/reglas, /api/v1/politica) ---------- */

export type ParametrosRegla = Record<string, number>;

export interface VersionAnteriorRegla {
  codigo: string;
  descripcion: string;
  tipo: string;
  umbral: number | null;
  parametros: ParametrosRegla;
  peso: number;
  activa: boolean;
  version: number;
  esquema_version: number;
  reemplazada_en: string;
}

export interface Regla {
  _id: string;
  codigo: string;
  descripcion: string;
  tipo: string;
  umbral: number | null;
  parametros: ParametrosRegla;
  peso: number;
  activa: boolean;
  version: number | null;
  esquema_version: number;
  versiones_anteriores: VersionAnteriorRegla[];
}

export interface ListaReglas {
  total: number;
  elementos: Regla[];
}

export interface TipoSoportado {
  tipo: string;
  umbral: string;
  parametros: { nombre: string; descripcion: string }[];
  evaluable: boolean;
}

export interface ListaTipos {
  elementos: TipoSoportado[];
}

/** Cuerpo de PATCH /api/v1/reglas/{id}: solo lo que cambia, mas `version_esperada`. */
export interface EdicionRegla {
  descripcion?: string;
  umbral?: number;
  parametros?: ParametrosRegla;
  peso?: number;
  activa?: boolean;
  version_esperada?: number;
}

export interface NuevaRegla {
  codigo: string;
  descripcion: string;
  tipo: string;
  umbral: number;
  parametros: ParametrosRegla;
  peso: number;
  activa: boolean;
}

export interface CorteSeveridad {
  severidad: Severidad;
  puntaje_minimo: number;
  puntaje_maximo: number;
}

export interface VersionAnteriorPolitica {
  version: number;
  umbral_alerta: number;
  puntaje_maximo: number;
  cortes_severidad: CorteSeveridad[];
  vigente_desde: string | null;
  reemplazada_en: string;
}

export interface PoliticaVigente {
  _id: string;
  descripcion: string;
  version: number;
  umbral_alerta: number;
  puntaje_maximo: number;
  cortes_severidad: CorteSeveridad[];
  vigente_desde: string | null;
  versiones_anteriores: VersionAnteriorPolitica[];
}

export interface EdicionPolitica {
  umbral_alerta?: number;
  puntaje_maximo?: number;
  cortes_severidad?: CorteSeveridad[];
  version_esperada?: number;
}

export interface ReglaDisparada {
  codigo: string;
  regla_id: string;
  version: number;
  peso: number;
  explicacion: string;
}

export interface ReglaNoEvaluada {
  codigo: string;
  regla_id: string;
  version: number | null;
  motivo: string;
}

/** Respuesta de POST /api/v1/reglas/simular. */
export interface Evaluacion {
  transaccion_id: string;
  puntaje: number;
  severidad: Severidad | null;
  genera_alerta: boolean;
  politica_id: string;
  politica_version: number;
  reglas_disparadas: ReglaDisparada[];
  reglas_no_evaluadas: ReglaNoEvaluada[];
}

/** GET /salud: el proceso vive y la base contesta. */
export interface SaludBase {
  estado: string;
  version: string;
  entorno: string;
  mongo_conectado: boolean;
  replica_set: string | null;
  es_primario: boolean | null;
  detalle: string | null;
}

export type EstadoTarea = "corriendo" | "degradada" | "retrasada" | "detenida" | "fallida" | "no_construida";

export interface TareaSupervisada {
  nombre: string;
  estado: EstadoTarea;
  eventos_procesados: number;
  ultimo_evento: string | null;
  retraso_segundos: number | null;
  ultimo_error: { mensaje: string; fecha: string } | null;
  historia: string | null;
  advertencia: string | null;
  latencia_ms: { n: number; p50: number | null; p95: number | null; max: number | null } | null;
  rezago: {
    pendientes: number;
    antiguedad_segundos: number | null;
    ritmo_por_segundo: number | null;
    segundos_para_ponerse_al_dia: number | null;
    atraso_segundos: number | null;
    medido_en: string;
  } | null;
}

/** GET /estado: el sistema esta haciendo su trabajo, o no. */
export interface EstadoFuncional {
  estado: "ok" | "degradado" | "sin_supervision";
  version: string;
  entorno: string;
  tolerancia_retraso_segundos: number;
  tareas: TareaSupervisada[];
  advertencias: string[];
}

// ---- Respaldos (D-74), solo administrador ----

export interface DiferenciaRespaldo {
  coleccion: string;
  esperados_desde: number;
  esperados_hasta: number;
  restaurados: number;
}

export interface VerificacionRespaldo {
  verificado_utc: string;
  verificado_por: string;
  resultado: "correcta" | "fallida";
  base_temporal: string;
  base_temporal_borrada: boolean;
  duracion_segundos: number;
  documentos_restaurados: Record<string, number>;
  diferencias: DiferenciaRespaldo[];
  detalle: string | null;
}

/** El manifiesto de un respaldo: lo que `mongodump` dejo junto al archivo. */
export interface Respaldo {
  esquema_version: number;
  respaldo_id: string;
  creado_utc: string;
  creado_por: string;
  base: string;
  archivo: string;
  tamano_bytes: number;
  sha256: string;
  herramienta: string;
  documentos_por_coleccion: Record<string, number>;
  documentos_al_terminar: Record<string, number>;
  verificacion: VerificacionRespaldo | null;
}

export interface ListaRespaldos {
  elementos: Respaldo[];
  total: number;
  en_curso: boolean;
}

/** Una notificacion de D2 (GET /api/v1/notificaciones): el correo de una alerta critica. */
export interface ElementoNotificacion {
  notificacion_id: string;
  alerta_id: string;
  canal: string;
  estado_envio: "pendiente" | "enviado" | "fallido";
  fecha: string;
  intentos: number | null;
  asunto: string | null;
  destinatarios: string[] | null;
  detalle_error: string | null;
  severidad: string | null;
  puntaje: number | null;
  monto_crc: number | null;
  cliente_nombre: string | null;
}

/** Una cuenta del portal (GET /api/v1/usuarios, solo administrador). */
export interface UsuarioPortal {
  agente_id: string;
  nombre: string;
  rol: Rol;
  activo: boolean;
  tiene_credencial: boolean;
  intentos_fallidos: number;
  bloqueado: boolean;
  /** UTC; solo mientras el bloqueo esta vigente. */
  bloqueado_hasta: string | null;
}

export interface ListaUsuarios {
  elementos: UsuarioPortal[];
  total: number;
  bloqueados: number;
}

/** La respuesta de POST /api/v1/sesion/renovar: la SPA solo usa `expira`. */
export interface SesionRenovada {
  agente_id: string;
  rol: Rol;
  expira: string;
}
