import type { MensajeError } from "../reglas/errores";

export type ResultadoAviso = { tipo: "ok"; texto: string } | { tipo: "error"; error: MensajeError } | null;

/**
 * El resultado de una accion, anunciado: `status` (cortes) si salio bien, `alert` si no.
 * Con `null` no dibuja nada; para que el lector de pantalla anuncie el aviso hace falta
 * que la region ya exista, asi que quien la usa la deja montada y cambia `resultado`.
 */
export function AvisoRegion({ resultado }: { resultado: ResultadoAviso }) {
  if (resultado === null) return null;
  if (resultado.tipo === "ok") {
    return (
      <div className="mensaje mensaje-ok" role="status" aria-live="polite">
        {resultado.texto}
      </div>
    );
  }
  return (
    <div className="mensaje mensaje-error" role="alert">
      {resultado.error.texto}
      {resultado.error.detalles.length > 0 ? (
        <ul>
          {resultado.error.detalles.map((detalle) => (
            <li key={detalle}>{detalle}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
