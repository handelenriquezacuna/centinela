/*
 * La cola del panel "Por alerta", mantenida en el navegador entre eventos.
 *
 * El servidor la manda ordenada con ORDEN_PANEL (app/repos/alertas.py): puntaje
 * descendente, fecha de creacion descendente y el identificador como desempate. Una
 * alerta que llega por el canal JSON trae la alerta completa: se reemplaza por `_id`
 * (cambio de estado) o se agrega (alerta nueva) y se reordena con la misma clave. No se
 * recalcula nada de la alerta aqui.
 */
import type { Alerta, Pagina } from "./api/tipos";

export function compararCola(a: Alerta, b: Alerta): number {
  return (
    b.puntaje - a.puntaje ||
    Date.parse(b.fecha_creacion) - Date.parse(a.fecha_creacion) ||
    (a._id < b._id ? -1 : a._id > b._id ? 1 : 0)
  );
}

export function ordenarCola(alertas: readonly Alerta[]): Alerta[] {
  return [...alertas].sort(compararCola);
}

/** La pagina con `alerta` agregada o reemplazada por su `_id`, reordenada. */
export function aplicarAlerta(pagina: Pagina<Alerta>, alerta: Alerta): Pagina<Alerta> {
  const estaba = pagina.elementos.some((a) => a._id === alerta._id);
  const resto = pagina.elementos.filter((a) => a._id !== alerta._id);
  return {
    ...pagina,
    total: pagina.total + (estaba ? 0 : 1),
    elementos: [...resto, alerta].sort(compararCola),
  };
}
