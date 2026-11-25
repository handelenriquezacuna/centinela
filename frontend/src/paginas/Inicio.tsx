import { Navigate } from "react-router";

import { useSesion } from "../api/sesion";

/** `/`: cada rol aterriza donde trabaja (`sesion.inicio`); sin sesion, al login. */
export function Inicio() {
  const { data: sesion, isPending, isError, error } = useSesion();
  if (isPending) return <p role="status">Cargando la sesión…</p>;
  if (isError) {
    return (
      <div className="mensaje mensaje-error" role="alert">
        No se pudo leer la sesión: {error.message}
      </div>
    );
  }
  return <Navigate to={sesion === null ? "/login" : sesion.inicio} replace />;
}
