import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

interface Propiedades {
  etiqueta: string;
  /** Un solo control (`input`, `select`, `textarea`): recibe `id` y los `aria-*` del campo. */
  children: ReactElement<Record<string, unknown>>;
  ayuda?: ReactNode;
  error?: string | null;
}

/** Etiqueta, control, ayuda y error enlazados: el lector de pantalla lee los cuatro juntos. */
export function FormularioCampo({ etiqueta, children, ayuda, error }: Propiedades) {
  const id = useId();
  const idAyuda = `${id}-ayuda`;
  const idError = `${id}-error`;
  const describe = [ayuda ? idAyuda : null, error ? idError : null].filter(Boolean).join(" ");
  const control = isValidElement(children)
    ? cloneElement(children, {
        id,
        "aria-describedby": describe || undefined,
        "aria-invalid": error ? true : undefined,
      })
    : children;
  return (
    <div className="campo">
      <label htmlFor={id}>{etiqueta}</label>
      {control}
      {ayuda ? (
        <small id={idAyuda} className="campo-ayuda">
          {ayuda}
        </small>
      ) : null}
      {error ? (
        <small id={idError} className="campo-error" role="alert">
          {error}
        </small>
      ) : null}
    </div>
  );
}
