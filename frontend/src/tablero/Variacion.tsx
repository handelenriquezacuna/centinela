import type { Comparacion } from "../api/tipos";
import { sentido, variacionTexto } from "./formatos";

interface Propiedades {
  valor: Comparacion;
  hayPrevio: boolean;
  unidad?: string;
  comoColones?: boolean;
  /** Sin la cola "contra el periodo anterior": para celdas de una tabla que ya lo dice. */
  breve?: boolean;
}

/** El cambio de un indicador contra el periodo anterior, en palabras (nunca solo color). */
export function Variacion({ valor, hayPrevio, unidad = "", comoColones = false, breve = false }: Propiedades) {
  const cambio = sentido(valor, hayPrevio);
  if (cambio === "sin_dato" || valor.delta === null) {
    return <>{breve ? "-" : "sin periodo anterior con que comparar"}</>;
  }
  if (cambio === "igual") {
    return <>{breve ? "sin cambio" : "sin cambio contra el periodo anterior"}</>;
  }
  return (
    <>
      <span className={cambio === "sube" ? "variacion-sube" : "variacion-baja"}>
        {variacionTexto(valor.delta, valor.porcentaje, unidad, comoColones)}
      </span>
      {breve ? "" : " contra el periodo anterior"}
    </>
  );
}
