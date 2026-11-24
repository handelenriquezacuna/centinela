/*
 * Formatos de Costa Rica para la SPA: el equivalente de app/api/formatos.py.
 *
 * La API manda dinero como entero de colones y fechas en UTC; aqui se traducen al
 * presentar, igual que en las plantillas HTMX:
 *
 * - montos: `₡750.000` (punto de miles, nunca centimos: SINPE Movil no los opera). Un
 *   monto que no es entero no se formatea: se devuelve vacio, para que un flotante
 *   colado se note en pantalla en vez de redondearse en silencio;
 * - fechas: `DD/MM/AAAA HH:MM` en hora de Costa Rica (UTC-6, sin horario de verano);
 * - decimales y porcentajes con coma: `11,2%`;
 * - claves internas en palabras: `en_revision` -> `En revisión`.
 *
 * `formatos.casos.json` tiene los casos que verifican las dos implementaciones.
 */

const ETIQUETAS: Record<string, string> = {
  nueva: "Nueva",
  en_revision: "En revisión",
  confirmada: "Confirmada",
  descartada: "Descartada",
  escalada: "Escalada",
  sinpe_movil: "SINPE Móvil",
  sin_asignar: "Sin asignar",
  CRITICA: "Crítica",
  ALTA: "Alta",
  MEDIA: "Media",
  BAJA: "Baja",
};

const ZONA_COSTA_RICA = "America/Costa_Rica";

const PARTES_FECHA = new Intl.DateTimeFormat("es-CR", {
  timeZone: ZONA_COSTA_RICA,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** Colones enteros como `₡750.000`. Vacio si no es un entero. */
export function colones(valor: unknown): string {
  if (typeof valor !== "number" || !Number.isSafeInteger(valor)) {
    return "";
  }
  const signo = valor < 0 ? "-" : "";
  const miles = Math.abs(valor)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${signo}₡${miles}`;
}

/** Fecha y hora de Costa Rica como `24/10/2026 06:00`. Vacio si no es una fecha. */
export function horaCostaRica(valor: unknown): string {
  if (typeof valor !== "string" && !(valor instanceof Date)) {
    return "";
  }
  const fecha = valor instanceof Date ? valor : new Date(valor);
  if (Number.isNaN(fecha.getTime())) {
    return "";
  }
  const partes: Record<string, string> = {};
  for (const parte of PARTES_FECHA.formatToParts(fecha)) {
    partes[parte.type] = parte.value;
  }
  return `${partes.day}/${partes.month}/${partes.year} ${partes.hour}:${partes.minute}`;
}

/** Un numero con coma decimal: `11,2`. */
export function decimal(valor: unknown, decimales = 1): string {
  if (typeof valor !== "number" || !Number.isFinite(valor)) {
    return "";
  }
  return valor.toFixed(decimales).replace(".", ",");
}

/** Un porcentaje con coma decimal: `12,5%`. El valor ya viene en porcentaje. */
export function porcentaje(valor: unknown, decimales = 1): string {
  const texto = decimal(valor, decimales);
  return texto ? `${texto}%` : "";
}

/** Estado o clave interna en palabras: `en_revision` -> `En revisión`. */
export function etiqueta(valor: unknown): string {
  if (valor === null || valor === undefined || valor === "") {
    return "";
  }
  const clave = String(valor);
  const conocida = ETIQUETAS[clave];
  if (conocida !== undefined) {
    return conocida;
  }
  const palabras = clave.replaceAll("_", " ");
  return palabras.charAt(0).toUpperCase() + palabras.slice(1);
}
