import { horaCostaRica } from "../formatos";

interface Propiedades {
  /** ISODate en UTC, como viene de la API. */
  valor: string | Date | null | undefined;
  /** `con_zona` agrega "(hora CR)" para las pantallas que no lo dicen en otro lado. */
  formato?: "corto" | "con_zona";
}

/** Una fecha a hora de Costa Rica; el `<time>` conserva el instante UTC para quien lo lea por maquina. */
export function FechaCR({ valor, formato = "corto" }: Propiedades) {
  const texto = horaCostaRica(valor);
  if (texto === "" || valor === null || valor === undefined) return null;
  const iso = valor instanceof Date ? valor.toISOString() : valor;
  return (
    <time className="hora" dateTime={iso}>
      {formato === "con_zona" ? `${texto} (hora CR)` : texto}
    </time>
  );
}
