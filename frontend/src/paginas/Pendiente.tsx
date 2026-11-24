interface Propiedades {
  titulo: string;
  endpoint: string;
}

/**
 * Pantalla que la fase 2 de T-61 todavia no migro. Dice que la reemplaza y de que
 * endpoint va a leer, para que nadie la confunda con un error.
 */
export function Pendiente({ titulo, endpoint }: Propiedades) {
  return (
    <section className="tarjeta" aria-labelledby="titulo-pendiente">
      <h1 id="titulo-pendiente" className="seccion-titulo">
        {titulo}
      </h1>
      <p>
        Esta pantalla todavía no está migrada a la nueva interfaz. Mientras tanto se usa con{" "}
        <code>portal.interfaz: htmx</code>.
      </p>
      <p className="nota-tecnica">
        Datos: <code>{endpoint}</code>
      </p>
    </section>
  );
}
