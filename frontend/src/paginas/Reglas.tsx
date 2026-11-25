import { useQuery } from "@tanstack/react-query";

import { pedir } from "../api/cliente";
import { useSesion } from "../api/sesion";
import type { ListaReglas, ListaTipos, PoliticaVigente, TipoSoportado } from "../api/tipos";
import { Cargando } from "../componentes/Cargando";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { Permitido } from "../componentes/Permitido";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { VistaError } from "../componentes/VistaError";
import { VistaVacia } from "../componentes/VistaVacia";
import { NuevaRegla } from "../reglas/NuevaRegla";
import { CLAVE_POLITICA, Politica } from "../reglas/Politica";
import { CLAVE_REGLAS, ReglaTarjeta } from "../reglas/ReglaTarjeta";
import { Simulador } from "../reglas/Simulador";

const CLAVE_TIPOS = ["tipos-regla"] as const;

const COLUMNAS_TIPOS: Columna<TipoSoportado>[] = [
  { clave: "tipo", titulo: "Tipo", celda: (t) => <code>{t.tipo}</code> },
  { clave: "umbral", titulo: "Qué significa el umbral", celda: (t) => t.umbral },
  {
    clave: "parametros",
    titulo: "Parámetros que lee",
    celda: (t) =>
      t.parametros.length === 0
        ? "ninguno"
        : t.parametros.map((p, i) => (
            <span key={p.nombre}>
              {i > 0 ? "; " : ""}
              <code>{p.nombre}</code> ({p.descripcion})
            </span>
          )),
  },
  { clave: "evaluable", titulo: "Evaluable", celda: (t) => (t.evaluable ? "Sí" : "No, todavía") },
];

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
    <MarcoPagina id="reglas" ancho="ancho">
      <EncabezadoPagina
        id="reglas"
        titulo="Reglas de detección"
        descripcion="Cada edición sube la versión y conserva la anterior. Si otra persona editó la regla en el medio, el guardado se rechaza y no se escribe nada."
      />

      {politica.isPending ? <Cargando texto="Cargando la política…" /> : null}
      {politica.isError ? (
        <VistaError
          error={{ texto: `No hay una política vigente en la base, o no se pudo leer: ${politica.error.message}`, detalles: [] }}
          alReintentar={() => void politica.refetch()}
        />
      ) : null}
      {politica.data ? <Politica politica={politica.data} puedeEditar={puedeEditar} /> : null}

      <section className="tarjeta" aria-labelledby="titulo-tipos">
        <h2 id="titulo-tipos" className="seccion-titulo">
          Tipos de regla que el motor sabe evaluar
        </h2>
        <TablaDatos
          leyenda="Tipos de regla"
          columnas={COLUMNAS_TIPOS}
          filas={tipos.data?.elementos ?? []}
          clave={(t) => t.tipo}
          estado={tipos.isPending ? "cargando" : tipos.isError ? "error" : "listo"}
          error={tipos.isError ? { texto: `No se pudieron leer los tipos: ${tipos.error.message}`, detalles: [] } : undefined}
          alReintentar={() => void tipos.refetch()}
        />
      </section>

      <Permitido permiso="editar_reglas">
        {tipos.data ? <NuevaRegla tipos={tipos.data.elementos} /> : null}
        {reglas.data ? <Simulador reglas={reglas.data.elementos} /> : null}
      </Permitido>

      <h2 className="seccion-titulo">Catálogo ({reglas.data?.total ?? 0})</h2>
      {reglas.isPending ? <Cargando texto="Cargando las reglas…" /> : null}
      {reglas.isError ? (
        <VistaError
          error={{ texto: `No se pudieron leer las reglas: ${reglas.error.message}`, detalles: [] }}
          alReintentar={() => void reglas.refetch()}
        />
      ) : null}
      {reglas.data?.elementos.map((regla) => <ReglaTarjeta key={regla._id} regla={regla} puedeEditar={puedeEditar} />)}
      {reglas.data?.elementos.length === 0 ? <VistaVacia titulo="No hay reglas en la base." /> : null}
    </MarcoPagina>
  );
}
