/*
 * El unico lugar de la SPA que llama a la API.
 *
 * - `credentials: "same-origin"`: la sesion viaja en la cookie HttpOnly que deja
 *   POST /api/v1/sesion. La SPA nunca ve ni guarda el token.
 * - Todo error de la API tiene la forma `{error: {codigo, mensaje, detalles}}` y se
 *   convierte en `ErrorApi`; un fallo de red tambien, con codigo `sin_conexion`.
 * - Un 401 avisa a quien se suscribio (`alPerderSesion`): la SPA vuelve al login
 *   conservando a donde iba.
 */
import type { CuerpoError } from "./tipos";

export class ErrorApi extends Error {
  readonly estado: number;
  readonly codigo: string;
  readonly detalles: CuerpoError["error"]["detalles"];

  constructor(estado: number, codigo: string, mensaje: string, detalles: CuerpoError["error"]["detalles"] = []) {
    super(mensaje);
    this.name = "ErrorApi";
    this.estado = estado;
    this.codigo = codigo;
    this.detalles = detalles;
  }
}

type Oyente = () => void;
const oyentesSesion = new Set<Oyente>();

/** Suscribe una funcion al 401 de cualquier peticion. Devuelve como desuscribirla. */
export function alPerderSesion(oyente: Oyente): () => void {
  oyentesSesion.add(oyente);
  return () => {
    oyentesSesion.delete(oyente);
  };
}

function esCuerpoError(valor: unknown): valor is CuerpoError {
  if (typeof valor !== "object" || valor === null || !("error" in valor)) {
    return false;
  }
  const error = (valor as { error: unknown }).error;
  return typeof error === "object" && error !== null && "codigo" in error && "mensaje" in error;
}

export interface OpcionesPeticion {
  metodo?: "GET" | "POST" | "PATCH" | "DELETE";
  cuerpo?: unknown;
  consulta?: Record<string, string | number | undefined>;
  /** No avisar del 401 (el propio login, o preguntar si hay sesion). */
  sinAvisoDeSesion?: boolean;
}

export async function pedir<T>(ruta: string, opciones: OpcionesPeticion = {}): Promise<T> {
  const consulta = new URLSearchParams();
  for (const [clave, valor] of Object.entries(opciones.consulta ?? {})) {
    if (valor !== undefined) {
      consulta.set(clave, String(valor));
    }
  }
  const url = consulta.size > 0 ? `${ruta}?${consulta.toString()}` : ruta;
  const init: RequestInit = {
    method: opciones.metodo ?? "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  };
  if (opciones.cuerpo !== undefined) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify(opciones.cuerpo);
  }

  let respuesta: Response;
  try {
    respuesta = await fetch(url, init);
  } catch {
    throw new ErrorApi(0, "sin_conexion", "No se pudo contactar al servidor");
  }

  if (respuesta.status === 204) {
    return undefined as T;
  }
  const datos: unknown = await respuesta.json().catch(() => null);
  if (respuesta.ok) {
    return datos as T;
  }
  if (respuesta.status === 401 && !opciones.sinAvisoDeSesion) {
    oyentesSesion.forEach((oyente) => oyente());
  }
  if (esCuerpoError(datos)) {
    throw new ErrorApi(respuesta.status, datos.error.codigo, datos.error.mensaje, datos.error.detalles);
  }
  throw new ErrorApi(respuesta.status, "error_interno", `El servidor respondió ${respuesta.status}`);
}
