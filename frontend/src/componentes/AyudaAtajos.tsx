import { forwardRef } from "react";

/** Dialogo modal con los atajos del panel. Foco y Esc son los nativos de <dialog>. */
export const AyudaAtajos = forwardRef<HTMLDialogElement>(function AyudaAtajos(_, ref) {
  return (
    <dialog id="ayuda-atajos" ref={ref} aria-labelledby="ayuda-atajos-titulo" className="dialogo">
      <form method="dialog">
        <h2 id="ayuda-atajos-titulo">Atajos de teclado</h2>
        <dl className="atajos">
          <div>
            <dt>
              <kbd>j</kbd> / <kbd>k</kbd>
            </dt>
            <dd>Bajar / subir una alerta</dd>
          </div>
          <div>
            <dt>
              <kbd>x</kbd>
            </dt>
            <dd>Seleccionar o quitar la alerta (vista por cuenta destino)</dd>
          </div>
          <div>
            <dt>
              <kbd>Enter</kbd>
            </dt>
            <dd>Abrir el detalle de la alerta</dd>
          </div>
          <div>
            <dt>
              <kbd>?</kbd>
            </dt>
            <dd>Mostrar u ocultar esta ayuda</dd>
          </div>
          <div>
            <dt>
              <kbd>Esc</kbd>
            </dt>
            <dd>Cerrar un diálogo</dd>
          </div>
        </dl>
        <p className="nota-tecnica">Los atajos no se activan mientras escribes en un campo.</p>
        <button type="submit" className="boton boton-secundario">
          Cerrar
        </button>
      </form>
    </dialog>
  );
});
