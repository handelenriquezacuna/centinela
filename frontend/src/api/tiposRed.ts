/*
 * Tipos de la red de mulas: GET /api/v1/red-mulas/{cuenta}/sankey y /grafo, y las
 * escrituras de listas_riesgo. Espejo de app/api/esquemas_portal.py y app/repos/red_mulas.py.
 * La SPA no calcula la disposicion: dibuja lo que el servidor ya dispuso (px enteros) y el
 * dinero llega y se queda como entero de colones.
 */

export interface NodoSankey {
  x: number;
  y: number;
  ancho: number;
  alto: number;
  cuenta: string;
  columna: number;
  externa: boolean;
  en_lista: boolean;
  inicio: boolean;
  victima: boolean;
  nombre: string | null;
}

export interface BandaSankey {
  d: string;
  grosor: number;
  tx: number;
  ty: number;
  salto: number;
  origen: string;
  destino: string;
  monto_crc: number;
  fecha: string;
  transaccion_id: string;
}

export interface CuentaSankey {
  victima: boolean;
  nombre: string | null;
  cuenta: string;
  cuenta_id: string | null;
  externa: boolean;
  en_lista: boolean;
  columna: number;
}

export interface DisposicionSankey {
  ancho: number;
  alto: number;
  columnas: number;
  saltos: number;
  transferencias: number;
  resumen: string;
  nodos: NodoSankey[];
  bandas: BandaSankey[];
  cuentas: CuentaSankey[];
}

export interface RespuestaSankey {
  cuenta: string;
  sankey: DisposicionSankey | null;
  motivo: string | null;
}

export interface NodoRed {
  cuenta: string;
  cuenta_id: string | null;
  externa: boolean;
  nivel: number;
  en_lista_riesgo: boolean;
}

export interface AristaRed {
  transaccion_id: string;
  origen: string;
  destino: string;
  monto_crc: number;
  fecha: string;
  salto: number;
  minutos_desde_anterior: number | null;
}

export interface RedMulas {
  cuenta: string;
  desde: string | null;
  ventana_minutos: number;
  saltos_maximos: number;
  saltos_recorridos: number;
  truncada: boolean;
  nodos: NodoRed[];
  aristas: AristaRed[];
  propuestas_lista_riesgo: string[];
}

export interface NodoGrafo {
  x: number;
  y: number;
  ancho: number;
  alto: number;
  cuenta: string;
  cuenta_id: string | null;
  externa: boolean;
  en_lista: boolean;
  inicio: boolean;
}

export interface AristaGrafo {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  tx: number;
  ty: number;
  monto_crc: number;
  minutos: number | null;
  salto: number;
}

export interface DisposicionGrafo {
  ancho: number;
  alto: number;
  nodos: NodoGrafo[];
  aristas: AristaGrafo[];
}

export interface EntradaListaRiesgo {
  _id: string;
  cuenta: string;
  motivo: string;
  fecha_reporte: string;
  vigente: boolean;
}

export interface RespuestaGrafo {
  red: RedMulas;
  grafo: DisposicionGrafo | null;
  entradas_lista: EntradaListaRiesgo[];
}

export interface ResultadoAprobacion {
  creada: boolean;
  entrada: EntradaListaRiesgo;
}

/** Parametros de la red que viajan por la URL y a las lecturas. */
export interface ParametrosRed {
  desde?: string | undefined;
  ventana_minutos?: string | undefined;
  saltos?: string | undefined;
}
