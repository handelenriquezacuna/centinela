import { useQuery } from "@tanstack/react-query";

import { pedir } from "../api/cliente";
import type { AvisoRezago as Aviso } from "../api/tipos";

const CADA_MS = 10_000;

/**
 * Aviso de D1 atrasado (T-57): GET /api/v1/deteccion/rezago cada 10 s, como el fragmento
 * HTMX. La region `role="status"` existe siempre para que el lector de pantalla anuncie
 * el aviso cuando aparece; vacia mientras D1 esta al dia. Se muestra si los pendientes pasan
 * `aviso_desde` (dice el conteo) o si el atraso en segundos pasa `aviso_atraso_desde` (dice
 * cuanto va detras de la ingesta): el atraso no depende del orden de los ids.
 */
export function AvisoRezago() {
  const { data } = useQuery({
    queryKey: ["rezago"],
    queryFn: () => pedir<Aviso>("/api/v1/deteccion/rezago"),
    refetchInterval: CADA_MS,
  });
  const pendientes = data?.pendientes ?? 0;

  return (
    <div id="aviso-rezago" role="status" aria-live="polite">
      {data?.atrasada ? (
        <div className="mensaje mensaje-error aviso-rezago">
          <strong>Detección atrasada:</strong>{" "}
          {data.por_conteo ? (
            <>
              {pendientes.toLocaleString("es-CR")} transacciones por evaluar
              {data.estimado ? `, unos ${data.estimado} para ponerse al día` : "; todavía sin ritmo medido"}. Las
              alertas de esas transacciones aparecerán cuando el detector se ponga al día.
            </>
          ) : (
            <>
              va unos {data.atraso_legible} detrás de la ingesta. Las alertas de las transacciones recientes
              aparecerán cuando el detector se ponga al día.
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
