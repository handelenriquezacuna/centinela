import type { ReactNode } from "react";

interface Propiedades {
  /** Identifica el titulo para `aria-labelledby`; el titulo (`h1`) lleva `${id}-titulo`. */
  id: string;
  ancho?: "normal" | "ancho";
  children: ReactNode;
}

/** La envoltura de una pantalla: una region con nombre, para que el lector de pantalla la anuncie. */
export function MarcoPagina({ id, ancho = "normal", children }: Propiedades) {
  return (
    <section className={ancho === "ancho" ? "marco-pagina marco-ancho" : "marco-pagina"} aria-labelledby={`${id}-titulo`}>
      {children}
    </section>
  );
}
