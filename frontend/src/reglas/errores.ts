import { ErrorApi } from "../api/cliente";

export interface MensajeError {
  texto: string;
  detalles: string[];
}

/**
 * Un fallo de la API en palabras para la pantalla. El 409 de concurrencia optimista lo
 * dice claro: alguien edito en el medio y no se escribio nada.
 */
export function mensajeDeError(fallo: unknown, sujeto: string, esConcurrencia = true): MensajeError {
  if (fallo instanceof ErrorApi) {
    const detalles = fallo.detalles.map((d) => (d.campo ? `${d.campo}: ${d.mensaje}` : d.mensaje));
    if (fallo.estado === 409 && esConcurrencia) {
      return {
        texto: `${sujeto} cambió mientras se editaba, así que no se guardó nada. Se cargó la versión vigente: revise los valores y vuelva a intentar.`,
        detalles,
      };
    }
    return { texto: fallo.message, detalles };
  }
  return { texto: "Ocurrió un error inesperado", detalles: [] };
}

/** Texto de un objeto de parametros: `{"ventana_minutos":30}`. */
export function parametrosATexto(parametros: Record<string, number>): string {
  return JSON.stringify(parametros);
}

/** Lee parametros escritos como JSON: un objeto de numeros, o un mensaje que dice que falla. */
export function leerParametros(texto: string): { valor: Record<string, number> } | { error: string } {
  let decodificado: unknown;
  try {
    decodificado = JSON.parse(texto.trim() === "" ? "{}" : texto);
  } catch {
    return { error: "los parámetros no son JSON válido" };
  }
  if (typeof decodificado !== "object" || decodificado === null || Array.isArray(decodificado)) {
    return { error: "los parámetros tienen que ser un objeto JSON" };
  }
  const valor: Record<string, number> = {};
  for (const [nombre, cantidad] of Object.entries(decodificado)) {
    if (typeof cantidad !== "number" || !Number.isFinite(cantidad)) {
      return { error: `el parámetro ${nombre} tiene que ser un número` };
    }
    valor[nombre] = cantidad;
  }
  return { valor };
}

export function sonIguales(a: Record<string, number>, b: Record<string, number>): boolean {
  const claves = Object.keys(a);
  return claves.length === Object.keys(b).length && claves.every((clave) => a[clave] === b[clave]);
}
