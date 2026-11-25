import { etiqueta } from "../formatos";

interface Propiedades {
  tipo: "severidad" | "estado";
  /** `CRITICA | ALTA | MEDIA` o `nueva | en_revision | confirmada | descartada | escalada`. */
  valor: string;
  /** Sustituye el texto por omision (`Severidad alta` en el detalle). */
  texto?: string;
}

/**
 * La insignia de severidad o de estado, con las clases de siempre. El color nunca es el
 * unico canal: lleva el texto, y la severidad una forma distinta (triangulo, diamante, cuadrado).
 */
export function Insignia({ tipo, valor, texto }: Propiedades) {
  const rotulo = texto ?? etiqueta(valor);
  if (tipo === "severidad") {
    return (
      <span className={`insignia es-${valor.toLowerCase()}`}>
        <span className="icono-severidad" aria-hidden="true" />
        {rotulo}
      </span>
    );
  }
  return <span className={`insignia insignia-estado es-${valor}`}>{rotulo}</span>;
}
