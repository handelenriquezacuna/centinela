import type { FormEvent, ReactNode } from "react";

interface Propiedades {
  /** Los controles, cada uno dentro de un `FormularioCampo`. */
  children: ReactNode;
  alBuscar: () => void;
  alLimpiar?: () => void;
  /** Nombre del grupo para el lector de pantalla. */
  rotulo: string;
  rotuloBuscar?: string;
}

/** La barra de filtros: un formulario con "Buscar" (Enter tambien) y "Limpiar". */
export function FiltroBarra({ children, alBuscar, alLimpiar, rotulo, rotuloBuscar = "Buscar" }: Propiedades) {
  function enviar(evento: FormEvent) {
    evento.preventDefault();
    alBuscar();
  }
  return (
    <form className="filtro-barra" role="search" aria-label={rotulo} onSubmit={enviar}>
      <div className="filtro-campos">{children}</div>
      <div className="filtro-acciones">
        <button type="submit" className="boton boton-primario">
          {rotuloBuscar}
        </button>
        {alLimpiar ? (
          <button type="button" className="boton boton-secundario" onClick={alLimpiar}>
            Limpiar
          </button>
        ) : null}
      </div>
    </form>
  );
}
