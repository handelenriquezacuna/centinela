import type { RespuestaCliente } from "../api/tipos";

interface Propiedades {
  respuesta: RespuestaCliente | null | undefined;
  /** Para que una prueba o un enlace interno la ubique. */
  id?: string;
}

/**
 * La respuesta del cliente a su SMS (T-82), como insignia. Es evidencia, no una decision:
 * "No fui yo" resalta (la alerta sube al tope de la cola) y "Si fui yo" es tenue.
 * El texto siempre acompana al color.
 */
export function InsigniaCliente({ respuesta, id }: Propiedades) {
  if (!respuesta) return null;
  const noFui = respuesta.valor === "no_fui_yo";
  return (
    <span
      id={id}
      className={`insignia insignia-cliente ${noFui ? "es-cliente-no" : "es-cliente-si"}`}
      title="Respuesta del cliente al SMS de aviso"
      data-respuesta-cliente={respuesta.valor}
    >
      {noFui ? "Cliente: no fui yo" : "Cliente: sí fui yo"}
    </span>
  );
}
