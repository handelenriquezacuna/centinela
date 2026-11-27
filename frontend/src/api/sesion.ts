/*
 * La sesion de la SPA. Entrar es POST /api/v1/sesion: el servidor deja la cookie
 * HttpOnly y SameSite=Strict, y el token que viene en el cuerpo se descarta sin
 * guardarlo. Quien soy es GET /api/v1/sesion; salir es DELETE /api/v1/sesion.
 */
import { useQuery } from "@tanstack/react-query";

import { ErrorApi, pedir } from "./cliente";
import type { SesionActual, SesionRenovada } from "./tipos";

export const CLAVE_SESION = ["sesion"] as const;

/** La sesion vigente, o `null` si no hay (401). Otros errores se propagan. */
export async function leerSesion(): Promise<SesionActual | null> {
  try {
    return await pedir<SesionActual>("/api/v1/sesion", { sinAvisoDeSesion: true });
  } catch (fallo) {
    if (fallo instanceof ErrorApi && fallo.estado === 401) {
      return null;
    }
    throw fallo;
  }
}

export function useSesion() {
  return useQuery({ queryKey: CLAVE_SESION, queryFn: leerSesion, staleTime: 60_000, retry: false });
}

export async function iniciarSesion(agenteId: string, contrasena: string): Promise<void> {
  // El cuerpo trae el token para clientes de la API; la SPA no lo lee ni lo guarda.
  await pedir<unknown>("/api/v1/sesion", {
    metodo: "POST",
    cuerpo: { agente_id: agenteId, contrasena },
    sinAvisoDeSesion: true,
  });
}

export async function cerrarSesion(): Promise<void> {
  await pedir<void>("/api/v1/sesion", { metodo: "DELETE", sinAvisoDeSesion: true });
}

/**
 * Renueva la sesion vigente (T-83): el servidor deja una cookie nueva con nueva expiracion.
 * El token del cuerpo se descarta, como en el login. Con una sesion vencida es un 401.
 */
export async function renovarSesion(): Promise<SesionRenovada> {
  return pedir<SesionRenovada>("/api/v1/sesion/renovar", { metodo: "POST", sinAvisoDeSesion: true });
}

/** Si la plantilla de una pantalla (`/detalle/{alerta_id}`) esta permitida a la sesion. */
export function puedeAbrir(sesion: SesionActual, plantilla: string): boolean {
  return sesion.paginas.includes(plantilla);
}
