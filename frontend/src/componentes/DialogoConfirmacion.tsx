import { useEffect, useId, useRef, type ReactNode } from "react";

interface Propiedades {
  abierto: boolean;
  titulo: string;
  /** El texto que dice que va a pasar. */
  children: ReactNode;
  etiquetaConfirmar?: string;
  /** `true` si la persona confirma; `false` al cancelar o cerrar con Esc. */
  alResolver: (confirmada: boolean) => void;
}

/**
 * Dialogo de confirmacion accesible: un <dialog> modal nativo, que atrapa el foco, se
 * cierra con Esc y devuelve el foco a quien lo abrio. El foco inicial queda en
 * "Cancelar" (el primer control): las acciones que se confirman aqui no se deshacen.
 */
export function DialogoConfirmacion({ abierto, titulo, children, etiquetaConfirmar = "Confirmar", alResolver }: Propiedades) {
  const dialogo = useRef<HTMLDialogElement>(null);
  const idTitulo = useId();
  const idTexto = useId();

  useEffect(() => {
    const el = dialogo.current;
    if (!el) return;
    if (abierto && !el.open) el.showModal();
    if (!abierto && el.open) el.close();
  }, [abierto]);

  return (
    <dialog
      ref={dialogo}
      className="dialogo"
      aria-labelledby={idTitulo}
      aria-describedby={idTexto}
      onClose={() => {
        // Esc: el navegador cerro el dialogo sin pasar por los botones.
        if (abierto) alResolver(false);
      }}
    >
      {abierto ? (
        <form onSubmit={(e) => e.preventDefault()}>
          <h2 id={idTitulo}>{titulo}</h2>
          <div id={idTexto}>{children}</div>
          <div className="grupo-botones">
            <button type="button" className="boton boton-secundario" onClick={() => alResolver(false)}>
              Cancelar
            </button>
            <button type="button" className="boton boton-primario" onClick={() => alResolver(true)}>
              {etiquetaConfirmar}
            </button>
          </div>
        </form>
      ) : null}
    </dialog>
  );
}
