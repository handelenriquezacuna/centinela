import type { ReactNode } from "react";

import type { MensajeError } from "../reglas/errores";
import { VistaError } from "./VistaError";
import { VistaVacia } from "./VistaVacia";

export interface Columna<T> {
  clave: string;
  titulo: string;
  celda: (fila: T) => ReactNode;
  /** Si la columna se puede ordenar: el titulo pasa a ser un boton. */
  ordenable?: boolean;
  clase?: string;
}

export interface Orden {
  columna: string;
  sentido: "asc" | "desc";
}

export interface Paginacion {
  desplazamiento: number;
  limite: number;
  total: number;
}

interface Propiedades<T> {
  /** Lo que la tabla contiene, para el lector de pantalla (`caption`). Obligatorio. */
  leyenda: string;
  columnas: Columna<T>[];
  filas: T[];
  clave: (fila: T) => string;
  estado: "cargando" | "error" | "listo";
  error?: MensajeError | undefined;
  alReintentar?: (() => void) | undefined;
  /** Que mostrar con `listo` y sin filas. */
  vacia?: ReactNode;
  orden?: Orden;
  alOrdenar?: (orden: Orden) => void;
  paginacion?: Paginacion;
  alCambiarPagina?: (desplazamiento: number) => void;
}

/**
 * La tabla de todas las pantallas: cargando, error con reintento, vacia, orden por columna
 * y paginacion con tope. En telefono cada fila pasa a tarjeta (`data-etiqueta` en cada celda).
 */
export function TablaDatos<T>({
  leyenda,
  columnas,
  filas,
  clave,
  estado,
  error,
  alReintentar,
  vacia,
  orden,
  alOrdenar,
  paginacion,
  alCambiarPagina,
}: Propiedades<T>) {
  if (estado === "cargando") {
    return <p role="status">Cargando…</p>;
  }
  if (estado === "error") {
    return <VistaError error={error ?? { texto: "No se pudo cargar la tabla", detalles: [] }} alReintentar={alReintentar} />;
  }
  if (filas.length === 0) {
    return <>{vacia ?? <VistaVacia titulo="No hay resultados" texto="Cambie los filtros o vuelva más tarde." />}</>;
  }

  function ordenarPor(columna: string) {
    const sentido = orden?.columna === columna && orden.sentido === "asc" ? "desc" : "asc";
    alOrdenar?.({ columna, sentido });
  }

  const hasta = paginacion ? Math.min(paginacion.desplazamiento + paginacion.limite, paginacion.total) : 0;
  return (
    <>
      <div className="tabla-envoltura">
        <table className="tabla-responsiva">
          <caption>{leyenda}</caption>
          <thead>
            <tr>
              {columnas.map((columna) => {
                const activa = orden?.columna === columna.clave;
                return (
                  <th
                    key={columna.clave}
                    scope="col"
                    className={columna.clase}
                    aria-sort={activa ? (orden.sentido === "asc" ? "ascending" : "descending") : undefined}
                  >
                    {columna.ordenable && alOrdenar ? (
                      <button type="button" className="orden-columna" onClick={() => ordenarPor(columna.clave)}>
                        {columna.titulo}
                        <span aria-hidden="true">{activa ? (orden.sentido === "asc" ? " ▲" : " ▼") : ""}</span>
                      </button>
                    ) : (
                      columna.titulo
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {filas.map((fila) => (
              <tr key={clave(fila)}>
                {columnas.map((columna) => (
                  <td key={columna.clave} data-etiqueta={columna.titulo} className={columna.clase}>
                    {columna.celda(fila)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {paginacion && alCambiarPagina ? (
        <nav className="paginacion" aria-label="Paginación">
          <span>
            {paginacion.desplazamiento + 1} a {hasta} de {paginacion.total}
          </span>
          <span className="grupo-botones">
            <button
              type="button"
              className="boton boton-secundario"
              disabled={paginacion.desplazamiento === 0}
              onClick={() => alCambiarPagina(Math.max(0, paginacion.desplazamiento - paginacion.limite))}
            >
              Anterior
            </button>
            <button
              type="button"
              className="boton boton-secundario"
              disabled={hasta >= paginacion.total}
              onClick={() => alCambiarPagina(paginacion.desplazamiento + paginacion.limite)}
            >
              Siguiente
            </button>
          </span>
        </nav>
      ) : null}
    </>
  );
}
