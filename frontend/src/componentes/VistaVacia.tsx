import type { ReactNode } from "react";

interface Propiedades {
  titulo: string;
  texto?: ReactNode;
  /** Siguiente paso: un boton o un enlace. */
  accion?: ReactNode;
}

/** Lo que se ve cuando no hay nada que mostrar: dice por que y que hacer, no deja un hueco. */
export function VistaVacia({ titulo, texto, accion }: Propiedades) {
  return (
    <div className="vista-vacia">
      <p className="vista-vacia-titulo">{titulo}</p>
      {texto ? <p className="vista-vacia-texto">{texto}</p> : null}
      {accion ? <div className="vista-vacia-accion">{accion}</div> : null}
    </div>
  );
}
