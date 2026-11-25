import type { ReactNode } from "react";

import { useSesion } from "../api/sesion";

interface Propiedades {
  /** Un permiso de `sesion.permisos` (`tablero`, `gestionar_listas`, `administracion`...). */
  permiso: string;
  children: ReactNode;
  /** Que mostrar sin el permiso; por omision, nada. */
  alternativa?: ReactNode;
}

/**
 * Muestra a quien tiene el permiso. Solo ordena la pantalla: quien decide de verdad es el
 * guardia del servidor, que responde 403 aunque la interfaz mostrara el boton.
 */
export function Permitido({ permiso, children, alternativa = null }: Propiedades) {
  const { data: sesion } = useSesion();
  return <>{sesion?.permisos.includes(permiso) ? children : alternativa}</>;
}
