import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { puedeAbrir, useSesion } from "../api/sesion";
import { rutaDeLogin } from "../destino";
import { Prohibido } from "../paginas/Prohibido";

interface Propiedades {
  /** Plantilla de la ruta tal como la declara la tabla de permisos: `/detalle/{alerta_id}`. */
  plantilla: string;
  children: ReactNode;
}

/**
 * Sin sesion, al login conservando el destino; con un rol que no puede abrir la pantalla,
 * acceso denegado. Quien decide de verdad es el guardia del servidor: esto solo evita
 * dibujar una pantalla cuyas peticiones iban a responder 401 o 403.
 */
export function RequiereSesion({ plantilla, children }: Propiedades) {
  const ubicacion = useLocation();
  const { data: sesion, isPending, isError, error } = useSesion();

  if (isPending) {
    return <p role="status">Cargando la sesión…</p>;
  }
  if (isError) {
    return (
      <div className="mensaje mensaje-error" role="alert">
        No se pudo leer la sesión: {error.message}
      </div>
    );
  }
  if (sesion === null) {
    return <Navigate to={rutaDeLogin(ubicacion.pathname + ubicacion.search)} replace />;
  }
  if (!puedeAbrir(sesion, plantilla)) {
    return <Prohibido plantilla={plantilla} />;
  }
  return <>{children}</>;
}
