import { Link } from "react-router";

import { useSesion } from "../api/sesion";

export function NoEncontrada() {
  const { data: sesion } = useSesion();
  return (
    <section className="tarjeta" aria-labelledby="titulo-no-encontrada">
      <h1 id="titulo-no-encontrada" className="seccion-titulo">
        Página no encontrada
      </h1>
      <p>
        Esa dirección no existe en el portal. <Link to={sesion?.inicio ?? "/login"}>Volver al inicio</Link>.
      </p>
    </section>
  );
}
