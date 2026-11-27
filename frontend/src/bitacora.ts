/*
 * Bitacora de auditoria (T-84): los filtros de la pantalla como parametros de
 * GET /api/v1/bitacora y la insignia de cada tipo de accion.
 *
 * - Vacio significa "sin filtro".
 * - Las fechas del formulario son dias de Costa Rica: `desde` es el inicio de ese dia y
 *   `hasta` el final, con el desfase de la zona (UTC-6, sin horario de verano).
 * - Una sola accion a la vez (el selector); la API acepta varias repetidas.
 */

export const CAMPOS_FILTRO_BITACORA = ["desde", "hasta", "actor", "accion", "objeto"] as const;
export type CampoFiltroBitacora = (typeof CAMPOS_FILTRO_BITACORA)[number];
export type FiltrosBitacora = Record<CampoFiltroBitacora, string>;

/** Los tipos de accion que la bitacora conoce, con su rotulo. `respuesta_cliente` llega con T-82. */
export const TIPOS_ACCION: readonly (readonly [valor: string, rotulo: string])[] = [
  ["alerta_en_revision", "Alerta tomada"],
  ["alerta_confirmada", "Alerta confirmada"],
  ["alerta_descartada", "Alerta descartada"],
  ["alerta_escalada", "Alerta escalada"],
  ["respuesta_cliente", "Respuesta del cliente"],
  ["regla_editada", "Regla editada"],
  ["politica_editada", "Política editada"],
  ["lista_riesgo_alta", "Cuenta marcada como mula"],
  ["caso_abierto", "Caso abierto"],
  ["notificacion_enviado", "Notificación enviada"],
  ["notificacion_fallido", "Notificación fallida"],
  ["notificacion_pendiente", "Notificación pendiente"],
  ["respaldo_creado", "Respaldo creado"],
  ["respaldo_verificado", "Respaldo verificado"],
];

const ROTULOS = new Map(TIPOS_ACCION);
const DESFASE_COSTA_RICA = "-06:00";
const DIA = /^\d{4}-\d{2}-\d{2}$/;
const TEXTO_SEGURO = /^[A-Za-z0-9-]{1,32}$/;

export interface ErrorFiltroBitacora {
  campo: string;
  mensaje: string;
}

export function filtrosBitacoraVacios(): FiltrosBitacora {
  return { desde: "", hasta: "", actor: "", accion: "", objeto: "" };
}

export function leerFiltrosBitacora(parametros: URLSearchParams): FiltrosBitacora {
  const filtros = filtrosBitacoraVacios();
  for (const campo of CAMPOS_FILTRO_BITACORA) {
    filtros[campo] = (parametros.get(campo) ?? "").trim();
  }
  return filtros;
}

function esDiaReal(texto: string): boolean {
  if (!DIA.test(texto)) return false;
  const fecha = new Date(`${texto}T00:00:00Z`);
  return !Number.isNaN(fecha.getTime()) && fecha.toISOString().startsWith(texto);
}

export function validarFiltrosBitacora(filtros: FiltrosBitacora): ErrorFiltroBitacora[] {
  const errores: ErrorFiltroBitacora[] = [];
  for (const campo of ["desde", "hasta"] as const) {
    if (filtros[campo] && !esDiaReal(filtros[campo])) {
      errores.push({ campo, mensaje: "debe ser una fecha AAAA-MM-DD" });
    }
  }
  for (const campo of ["actor", "objeto"] as const) {
    if (filtros[campo] && !TEXTO_SEGURO.test(filtros[campo])) {
      errores.push({ campo, mensaje: "solo letras, dígitos y guiones, hasta 32 caracteres" });
    }
  }
  if (errores.length === 0 && filtros.desde && filtros.hasta && filtros.desde > filtros.hasta) {
    errores.push({ campo: "desde", mensaje: "no puede ser posterior a hasta" });
  }
  return errores;
}

/** Los filtros como los entiende la API: solo los que traen valor, fechas con zona. */
export function parametrosDeApiBitacora(filtros: FiltrosBitacora): Record<string, string> {
  const salida: Record<string, string> = {};
  for (const campo of CAMPOS_FILTRO_BITACORA) {
    const valor = filtros[campo];
    if (!valor) continue;
    if (campo === "desde") salida[campo] = `${valor}T00:00:00${DESFASE_COSTA_RICA}`;
    else if (campo === "hasta") salida[campo] = `${valor}T23:59:59.999999${DESFASE_COSTA_RICA}`;
    else salida[campo] = valor;
  }
  return salida;
}

export function rotuloDeAccion(accion: string): string {
  return ROTULOS.get(accion) ?? accion.replaceAll("_", " ");
}

/**
 * La clase de `Insignia tipo="estado"` de una accion: las de alertas y envios reusan la paleta
 * de estados (`en_revision`, `confirmada`, `envio-enviado`...); el resto tiene la suya
 * (`accion-regla`, `accion-lista`, `accion-caso`, `accion-respaldo`). Una accion desconocida cae
 * en la insignia neutra.
 */
export function valorDeInsignia(accion: string): string {
  if (accion.startsWith("alerta_")) return accion.slice("alerta_".length);
  if (accion.startsWith("notificacion_")) return `envio-${accion.slice("notificacion_".length)}`;
  if (accion === "regla_editada" || accion === "politica_editada") return "accion-regla";
  if (accion === "lista_riesgo_alta") return "accion-lista";
  if (accion === "caso_abierto") return "accion-caso";
  if (accion.startsWith("respaldo_")) return "accion-respaldo";
  if (accion === "respuesta_cliente") return "accion-cliente";
  return "accion-otra";
}
