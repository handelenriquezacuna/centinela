import { Link } from "react-router";

import { useSesion } from "../api/sesion";
import { quienPuedeAbrir } from "../roles";

/** Acceso denegado: dice quien puede abrir la pantalla, con que rol se entro y adonde ir. */
export function Prohibido({ plantilla }: { plantilla?: string }) {
  const { data: sesion } = useSesion();
  const quien = plantilla ? quienPuedeAbrir(plantilla) : null;
  return (
    <section className="tarjeta" aria-labelledby="titulo-prohibido">
      <h1 id="titulo-prohibido" className="seccion-titulo">
        Acceso denegado
      </h1>
      <p>
        {quien ? `Esta pantalla es para ${quien}.` : "Tu rol no permite abrir esta pantalla."}
        {sesion ? ` Entraste como ${sesion.rol}.` : ""}
      </p>
      {sesion ? (
        <p>
          <Link to={sesion.inicio}>Ir a mi pantalla de inicio</Link>
        </p>
      ) : null}
    </section>
  );
}
