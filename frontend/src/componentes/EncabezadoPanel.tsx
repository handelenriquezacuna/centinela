import type { ReactNode } from "react";
import { Link } from "react-router";

import type { EstadoCanal } from "../tiempo-real/eventosAlertas";

export type VistaPanel = "casos" | "alertas";

interface Propiedades {
  vista: VistaPanel;
  descripcion: ReactNode;
  estadoCanal: EstadoCanal;
  aviso: string;
  rotuloActualizar: string;
  alActualizar: () => void;
  alAbrirAyuda: () => void;
}

/**
 * Titulo, estado del canal, conmutador de vista y botones del panel. Las dos vistas lo
 * comparten: los mismos ids (`estado-canal`, `aviso-nuevas`) que la pantalla HTMX.
 */
export function EncabezadoPanel({
  vista,
  descripcion,
  estadoCanal,
  aviso,
  rotuloActualizar,
  alActualizar,
  alAbrirAyuda,
}: Propiedades) {
  return (
    <div className="titulo-pantalla">
      <div>
        <h1>Panel de alertas</h1>
        <p className="subtitulo">
          {descripcion}{" "}
          <span className="estado-canal">
            Canal:{" "}
            <strong id="estado-canal" aria-live="polite">
              {estadoCanal}
            </strong>
          </span>
        </p>
        <p id="aviso-nuevas" className="subtitulo" aria-live="polite">
          {aviso}
        </p>
      </div>
      <div className="grupo-botones">
        <nav className="conmutador-vista" aria-label="Vista de la cola">
          <Link to="/panel?vista=casos" aria-current={vista === "casos" ? "page" : undefined}>
            Por cuenta destino
          </Link>
          <Link to="/panel?vista=alertas" aria-current={vista === "alertas" ? "page" : undefined}>
            Por alerta
          </Link>
        </nav>
        <button type="button" className="boton boton-secundario" onClick={alActualizar}>
          {rotuloActualizar}
        </button>
        <button type="button" className="boton boton-secundario" id="abrir-ayuda" aria-haspopup="dialog" onClick={alAbrirAyuda}>
          Atajos (?)
        </button>
      </div>
    </div>
  );
}
