/*
 * La lista de cuentas destino del panel, mantenida en el navegador entre eventos.
 *
 * El servidor ya la manda ordenada; cuando llega una alerta por el canal se pide de nuevo
 * SOLO el caso de su cuenta (GET /api/v1/casos-destino/{cuenta}) y se reemplaza, se
 * agrega o se quita. Despues se reordena con la misma clave que `clave_de_orden` de
 * app/repos/casos_destino.py: severidad maxima, puntaje maximo, la mas antigua primero y
 * la cuenta como desempate. No se recalcula nada del caso aqui.
 */
import type { CasoDestino, Pagina, Severidad } from "./api/tipos";

const RANGO: Record<Severidad, number> = { CRITICA: 3, ALTA: 2, MEDIA: 1 };

export function compararCasos(a: CasoDestino, b: CasoDestino): number {
  return (
    RANGO[b.severidad_max] - RANGO[a.severidad_max] ||
    b.puntaje_max - a.puntaje_max ||
    Date.parse(a.mas_antigua) - Date.parse(b.mas_antigua) ||
    (a.cuenta_destino < b.cuenta_destino ? -1 : a.cuenta_destino > b.cuenta_destino ? 1 : 0)
  );
}

/**
 * La pagina con el caso de `cuenta` actualizado. `caso` nulo = la cuenta ya no tiene
 * alertas abiertas y sale de la lista. El total se ajusta solo si entra o sale una cuenta.
 */
export function aplicarCaso(
  pagina: Pagina<CasoDestino>,
  cuenta: string,
  caso: CasoDestino | null,
): Pagina<CasoDestino> {
  const estaba = pagina.elementos.some((c) => c.cuenta_destino === cuenta);
  const resto = pagina.elementos.filter((c) => c.cuenta_destino !== cuenta);
  const elementos = caso === null ? resto : [...resto, caso].sort(compararCasos);
  const total = pagina.total + (caso !== null && !estaba ? 1 : 0) - (caso === null && estaba ? 1 : 0);
  return { ...pagina, total: Math.max(0, total), elementos };
}
