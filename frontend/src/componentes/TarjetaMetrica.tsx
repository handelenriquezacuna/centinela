import type { ReactNode } from "react";

interface Propiedades {
  rotulo: string;
  valor: ReactNode;
  /** Una linea bajo la cifra: la variacion, la medicion, la advertencia. */
  nota?: ReactNode;
  /** `sin_datos`: no hay cifra todavia; se dice con una frase corta y no con un titular gigante. */
  estado?: "normal" | "sin_datos" | "alerta";
  id?: string;
}

/** Una cifra con su rotulo. Sin datos se muestra un guion y la causa, no "sin resueltas" en 40 px. */
export function TarjetaMetrica({ rotulo, valor, nota, estado = "normal", id }: Propiedades) {
  return (
    <div className={`metrica metrica-${estado}`} id={id}>
      <span className="metrica-valor">{estado === "sin_datos" ? "—" : valor}</span>
      <span className="metrica-rotulo">{rotulo}</span>
      {nota ? <span className="metrica-nota">{nota}</span> : null}
    </div>
  );
}
