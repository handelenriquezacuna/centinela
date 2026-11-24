import { useQuery } from "@tanstack/react-query";

import { pedir } from "../api/cliente";
import { useSesion } from "../api/sesion";
import type { ListaReglas, ListaTipos, PoliticaVigente } from "../api/tipos";
import { NuevaRegla } from "../reglas/NuevaRegla";
import { CLAVE_POLITICA, Politica } from "../reglas/Politica";
import { CLAVE_REGLAS, ReglaTarjeta } from "../reglas/ReglaTarjeta";
import { Simulador } from "../reglas/Simulador";

const CLAVE_TIPOS = ["tipos-regla"] as const;

/**
 * Reglas y politica (T-16, T-20). Ver es de supervisor y administrador, y editar tambien;
 * la sesion (GET /api/v1/sesion) dice si el boton de guardar se muestra, y el servidor
 * lo decide de verdad.
 */
export function Reglas() {
  const { data: sesion } = useSesion();
  const puedeEditar = sesion?.permisos.includes("editar_reglas") ?? false;
  const reglas = useQuery({ queryKey: CLAVE_REGLAS, queryFn: () => pedir<ListaReglas>("/api/v1/reglas") });
  const tipos = useQuery({ queryKey: CLAVE_TIPOS, queryFn: () => pedir<ListaTipos>("/api/v1/reglas/tipos") });
  const politica = useQuery({ queryKey: CLAVE_POLITICA, queryFn: () => pedir<PoliticaVigente>("/api/v1/politica") });

  return (
    <>
      <div className="titulo-pantalla">
        <div>
          <h1>Reglas de detección</h1>
          <p className="subtitulo">
            Cada edición sube la versión y conserva la anterior. Si otra persona editó la regla en el medio, el guardado se rechaza con un 409 y no se escribe nada.
          </p>
        </div>
      </div>

      {politica.isPending ? <p role="status">Cargando la política…</p> : null}
      {politica.isError ? (
        <div className="mensaje mensaje-error" role="alert">
          No hay una política vigente en la base, o no se pudo leer: {politica.error.message}
        </div>
      ) : null}
      {politica.data ? <Politica politica={politica.data} puedeEditar={puedeEditar} /> : null}

      <section className="tarjeta" aria-labelledby="titulo-tipos">
        <h2 id="titulo-tipos" className="seccion-titulo">
          Tipos de regla que el motor sabe evaluar
        </h2>
        {tipos.isError ? (
          <p className="mensaje mensaje-error" role="alert">
            No se pudieron leer los tipos: {tipos.error.message}
          </p>
        ) : null}
        <table className="tabla-simple">
          <caption className="visualmente-oculto">Tipos de regla</caption>
          <thead>
            <tr>
              <th scope="col">Tipo</th>
              <th scope="col">Qué significa el umbral</th>
              <th scope="col">Parámetros que lee</th>
              <th scope="col">Evaluable</th>
            </tr>
          </thead>
          <tbody>
            {(tipos.data?.elementos ?? []).map((t) => (
              <tr key={t.tipo}>
                <td>
                  <code>{t.tipo}</code>
                </td>
                <td>{t.umbral}</td>
                <td>
                  {t.parametros.length === 0
                    ? "ninguno"
                    : t.parametros.map((p, i) => (
                        <span key={p.nombre}>
                          {i > 0 ? "; " : ""}
                          <code>{p.nombre}</code> ({p.descripcion})
                        </span>
                      ))}
                </td>
                <td>{t.evaluable ? "Sí" : "No, todavía"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {puedeEditar && tipos.data ? <NuevaRegla tipos={tipos.data.elementos} /> : null}
      {puedeEditar && reglas.data ? <Simulador reglas={reglas.data.elementos} /> : null}

      <h2 className="seccion-titulo">Catálogo ({reglas.data?.total ?? 0})</h2>
      {reglas.isPending ? <p role="status">Cargando las reglas…</p> : null}
      {reglas.isError ? (
        <div className="mensaje mensaje-error" role="alert">
          No se pudieron leer las reglas: {reglas.error.message}
        </div>
      ) : null}
      {reglas.data?.elementos.map((regla) => <ReglaTarjeta key={regla._id} regla={regla} puedeEditar={puedeEditar} />)}
      {reglas.data?.elementos.length === 0 ? <p className="subtitulo">No hay reglas en la base.</p> : null}
    </>
  );
}
