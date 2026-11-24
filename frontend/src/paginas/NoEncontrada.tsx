import { Link } from "react-router";

export function NoEncontrada() {
  return (
    <section className="tarjeta" aria-labelledby="titulo-no-encontrada">
      <h1 id="titulo-no-encontrada" className="seccion-titulo">
        Página no encontrada
      </h1>
      <p>
        Esa dirección no existe en el portal. <Link to="/panel">Volver al panel</Link>.
      </p>
    </section>
  );
}
