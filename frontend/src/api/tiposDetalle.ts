/*
 * Tipos de GET /api/v1/alertas/{id}/detalle y POST /api/v1/alertas/{id}/transiciones.
 * Espejo de `DetalleAlerta` (app/api/esquemas_portal.py). Dinero entero, fechas UTC.
 */
import type { EstadoAlerta, Severidad } from "./tipos";

export interface EntradaHistorial {
  fecha: string;
  agente_id: string | null;
  accion: string;
  nota?: string | null;
}

export interface AlertaDetallada {
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
  /** v2: objetos con la regla; v1: solo identificadores de regla. */
  reglas_disparadas: unknown[];
  historial: EntradaHistorial[];
}

export interface ReglaExplicada {
  regla_id: string;
  codigo: string;
  version: number;
  peso: number;
  explicacion: string;
  evidencia: string | null;
  acumulado: number;
  ancho: number;
}

export interface CorteExplicado {
  severidad: string;
  minimo: number;
  maximo: number;
  inicio: number;
  alcanzado: boolean;
}

export interface ExplicacionPuntaje {
  filas: ReglaExplicada[];
  suma: number;
  tope: boolean;
  maximo: number;
  cortes: CorteExplicado[];
  ancho_puntaje: number;
  umbral: number | null;
}

export interface AccionDisponible {
  destino: EstadoAlerta;
  rotulo: string;
  ayuda: string;
  /** La accion cierra la alerta: la interfaz pide confirmar. */
  cierra: boolean;
}

export interface TransaccionDetalle {
  transaccion_id: string;
  cuenta_origen: string;
  sinpe_origen: string | null;
  cuenta_destino: string;
  monto_crc: number;
  moneda: string;
  canal: string | null;
  fecha: string;
}

export interface ClienteDetalle {
  cliente_id: string;
  nombre: string;
  cedula: string | null;
}

export interface PerfilDetalle {
  promedio_crc: number | null;
  maximo_crc: number | null;
  num_transferencias: number | null;
  horario_habitual: { inicio: string | null; fin: string | null } | null;
  destinos_frecuentes: string[];
}

export interface PoliticaDetalle {
  politica_id: string | null;
  version: number | null;
  origen: "usada" | "vigente" | "ninguna";
}

export interface DetalleAlerta {
  alerta: AlertaDetallada;
  es_v2: boolean;
  aviso_v1: string | null;
  transaccion: TransaccionDetalle | null;
  cliente: ClienteDetalle | null;
  perfil: PerfilDetalle | null;
  politica: PoliticaDetalle;
  puntaje: ExplicacionPuntaje;
  acciones: AccionDisponible[];
  caso_id: string | null;
  nombres_agentes: Record<string, string>;
}

export interface ResultadoTransicion {
  alerta: AlertaDetallada;
  caso_id: string | null;
}
