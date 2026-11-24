/*
 * Filtros de la busqueda forense: del formulario (texto) a los parametros de
 * GET /api/v1/transacciones, con las mismas reglas que `leer_filtro` de
 * app/api/rutas/paginas_busqueda.py.
 *
 * - Vacio significa "sin filtro".
 * - Las fechas del formulario son dias de Costa Rica: `desde` es el inicio de ese dia y
 *   `hasta` el final, con el desfase de la zona (UTC-6, sin horario de verano) para que
 *   la API las convierta a UTC.
 * - Los montos son colones enteros: nada de decimales ni signos.
 * - El rango invertido lo rechaza tambien la API (422); aqui se avisa antes de pedir.
 */

export const CAMPOS_FILTRO = ["desde", "hasta", "monto_min", "monto_max", "cuenta", "canal", "estado_alerta"] as const;
export type CampoFiltro = (typeof CAMPOS_FILTRO)[number];
export type Filtros = Record<CampoFiltro, string>;

export const OPCIONES_LIMITE = [10, 25, 50, 100] as const;
export const CANALES = ["sinpe_movil"] as const;
export const ESTADOS_ALERTA = ["nueva", "en_revision", "confirmada", "descartada", "escalada"] as const;

const DESFASE_COSTA_RICA = "-06:00";
const ENTERO = /^\d{1,15}$/;
const DIA = /^\d{4}-\d{2}-\d{2}$/;

export interface ErrorFiltro {
  campo: string;
  mensaje: string;
}

export function filtrosVacios(): Filtros {
  return { desde: "", hasta: "", monto_min: "", monto_max: "", cuenta: "", canal: "", estado_alerta: "" };
}

export function leerFiltros(parametros: URLSearchParams): Filtros {
  const filtros = filtrosVacios();
  for (const campo of CAMPOS_FILTRO) {
    filtros[campo] = (parametros.get(campo) ?? "").trim();
  }
  return filtros;
}

function esDiaReal(texto: string): boolean {
  if (!DIA.test(texto)) return false;
  const fecha = new Date(`${texto}T00:00:00Z`);
  return !Number.isNaN(fecha.getTime()) && fecha.toISOString().startsWith(texto);
}

export function validarFiltros(filtros: Filtros): ErrorFiltro[] {
  const errores: ErrorFiltro[] = [];
  for (const campo of ["desde", "hasta"] as const) {
    if (filtros[campo] && !esDiaReal(filtros[campo])) {
      errores.push({ campo, mensaje: "debe ser una fecha AAAA-MM-DD" });
    }
  }
  for (const campo of ["monto_min", "monto_max"] as const) {
    if (filtros[campo] && !ENTERO.test(filtros[campo])) {
      errores.push({ campo, mensaje: "debe ser un entero en colones, sin decimales" });
    }
  }
  if (filtros.canal && !(CANALES as readonly string[]).includes(filtros.canal)) {
    errores.push({ campo: "canal", mensaje: "canal desconocido" });
  }
  if (filtros.estado_alerta && !(ESTADOS_ALERTA as readonly string[]).includes(filtros.estado_alerta)) {
    errores.push({ campo: "estado_alerta", mensaje: "estado desconocido" });
  }
  if (errores.length === 0) {
    if (filtros.desde && filtros.hasta && filtros.desde > filtros.hasta) {
      errores.push({ campo: "desde", mensaje: "no puede ser posterior a hasta" });
    }
    if (filtros.monto_min && filtros.monto_max && Number(filtros.monto_min) > Number(filtros.monto_max)) {
      errores.push({ campo: "monto_min", mensaje: "no puede ser mayor que monto_max" });
    }
  }
  return errores;
}

/** Los filtros como los entiende la API: solo los que traen valor, fechas con zona. */
export function parametrosDeApi(filtros: Filtros): Record<string, string> {
  const salida: Record<string, string> = {};
  for (const campo of CAMPOS_FILTRO) {
    const valor = filtros[campo];
    if (!valor) continue;
    if (campo === "desde") salida[campo] = `${valor}T00:00:00${DESFASE_COSTA_RICA}`;
    else if (campo === "hasta") salida[campo] = `${valor}T23:59:59.999999${DESFASE_COSTA_RICA}`;
    else salida[campo] = valor;
  }
  return salida;
}
