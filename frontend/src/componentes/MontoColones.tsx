import { colones } from "../formatos";

/** Un monto en colones enteros (`₡750.000`), en tipografia de cifras. Vacio si no es un entero. */
export function MontoColones({ valor }: { valor: number | null | undefined }) {
  const texto = colones(valor);
  return texto === "" ? null : <span className="monto">{texto}</span>;
}
