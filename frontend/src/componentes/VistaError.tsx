import type { MensajeError } from "../reglas/errores";

interface Propiedades {
  error: MensajeError;
  alReintentar?: (() => void) | undefined;
}

/** Un error de la API en palabras, con `role="alert"` y, si se puede, el boton de reintentar. */
export function VistaError({ error, alReintentar }: Propiedades) {
  return (
    <div className="mensaje mensaje-error" role="alert">
      <p>{error.texto}</p>
      {error.detalles.length > 0 ? (
        <ul>
          {error.detalles.map((detalle) => (
            <li key={detalle}>{detalle}</li>
          ))}
        </ul>
      ) : null}
      {alReintentar ? (
        <button type="button" className="boton boton-secundario" onClick={alReintentar}>
          Reintentar
        </button>
      ) : null}
    </div>
  );
}
