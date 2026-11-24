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
}

export interface SesionActual {
  agente_id: string;
  rol: Rol;
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
  atrasada: boolean;
  pendientes: number | null;
  aviso_desde: number;
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
