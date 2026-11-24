import type { MensajeError } from "./errores";

export type Resultado = { tipo: "ok"; texto: string } | { tipo: "error"; error: MensajeError } | null;

/** El resultado de guardar: `status` si salio bien, `alert` si no. */
export function Aviso({ resultado }: { resultado: Resultado }) {
  if (resultado === null) return null;
  if (resultado.tipo === "ok") {
    return (
      <div className="mensaje mensaje-ok" role="status">
        {resultado.texto}
      </div>
    );
  }
  return (
    <div className="mensaje mensaje-error" role="alert">
      {resultado.error.texto}
      {resultado.error.detalles.length > 0 ? (
        <ul>
          {resultado.error.detalles.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
