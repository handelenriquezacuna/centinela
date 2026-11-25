/** El estado "cargando" de toda pantalla: un solo texto anunciado, sin parpadeos ni esqueletos. */
export function Cargando({ texto = "Cargando…" }: { texto?: string }) {
  return (
    <p role="status" className="cargando">
      {texto}
    </p>
  );
}
