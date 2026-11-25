import type { ReactNode } from "react";
import { Link } from "react-router";

interface Miga {
  rotulo: string;
  a?: string;
}

interface Propiedades {
  /** El mismo `id` de `MarcoPagina`. */
  id: string;
  titulo: string;
  descripcion?: ReactNode;
  /** Botones a la derecha del titulo. */
  acciones?: ReactNode;
  migas?: Miga[];
}

/** Titulo (`h1`), descripcion, migas y acciones de una pantalla: igual en todas. */
export function EncabezadoPagina({ id, titulo, descripcion, acciones, migas }: Propiedades) {
  return (
    <header className="titulo-pantalla">
      <div>
        {migas && migas.length > 0 ? (
          <nav className="migaja" aria-label="Ruta de la pantalla">
            {migas.map((miga, indice) => (
              <span key={miga.rotulo}>
                {indice > 0 ? " / " : ""}
                {miga.a ? <Link to={miga.a}>{miga.rotulo}</Link> : miga.rotulo}
              </span>
            ))}
          </nav>
        ) : null}
        <h1 id={`${id}-titulo`}>{titulo}</h1>
        {descripcion ? <p className="subtitulo">{descripcion}</p> : null}
      </div>
      {acciones ? <div className="grupo-botones">{acciones}</div> : null}
    </header>
  );
}
