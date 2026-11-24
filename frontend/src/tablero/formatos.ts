/*
 * Textos del tablero. El servidor ya calculo cada cifra; aqui solo se escriben en
 * espanol de Costa Rica: coma decimal, colones con punto de miles, tiempos legibles.
 */
import { colones, decimal } from "../formatos";
import type { Comparacion } from "../api/tipos";

/** 90 -> `1 min 30 s`; 3700 -> `1 h 1 min`. El equivalente de `tiempo_corto` del servidor. */
export function tiempoCorto(segundos: number | null): string {
  if (segundos === null) return "-";
  const total = Math.round(segundos);
  const horas = Math.floor(total / 3600);
  const minutos = Math.floor((total % 3600) / 60);
  const seg = total % 60;
  if (horas > 0) return `${horas} h ${minutos} min`;
  if (minutos > 0) return `${minutos} min ${seg} s`;
  return `${seg} s`;
}

/** Un entero tal cual; un decimal con coma. */
export function numero(valor: number): string {
  return Number.isInteger(valor) ? String(valor) : decimal(valor, 1);
}

/** `+5` o `-3`, con su porcentaje entre parentesis si lo hay. */
export function variacionTexto(delta: number, porcentaje: number | null, unidad = "", comoColones = false): string {
  const magnitud = comoColones ? colones(Math.abs(delta)) : `${numero(Math.abs(delta))}${unidad}`;
  const signo = delta > 0 ? "+" : delta < 0 ? "-" : "";
  const pct = porcentaje === null ? "" : ` (${decimal(porcentaje, 1)}%)`;
  return `${signo}${magnitud}${pct}`;
}

export type SentidoVariacion = "sube" | "baja" | "igual" | "sin_dato";

/** Como cambio un indicador contra el periodo anterior. */
export function sentido(valor: Comparacion, hayPrevio: boolean): SentidoVariacion {
  if (!hayPrevio || valor.delta === null) return "sin_dato";
  if (valor.delta > 0) return "sube";
  if (valor.delta < 0) return "baja";
  return "igual";
}
