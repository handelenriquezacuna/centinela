import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { pedir } from "../api/cliente";
import type { ElementoBitacora, Pagina } from "../api/tipos";
import {
  TIPOS_ACCION,
  filtrosBitacoraVacios,
  leerFiltrosBitacora,
  parametrosDeApiBitacora,
  rotuloDeAccion,
  validarFiltrosBitacora,
  valorDeInsignia,
  type FiltrosBitacora,
} from "../bitacora";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { FiltroBarra } from "../componentes/FiltroBarra";
import { FormularioCampo } from "../componentes/FormularioCampo";
import { Insignia } from "../componentes/Insignia";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { VistaError } from "../componentes/VistaError";
import { VistaVacia } from "../componentes/VistaVacia";
import { mensajeDeError } from "../reglas/errores";

const LIMITE = 50;
const RUTA_API = "/api/v1/bitacora";
const LIMITE_EXPORTACION = 10_000;

/** El objeto como enlace cuando es una alerta; el resto se muestra tal cual. */
function Objeto({ valor }: { valor: string }) {
  return valor.startsWith("ALR-") ? <Link to={`/detalle/${valor}`}>{valor}</Link> : <>{valor}</>;
}

const COLUMNAS: Columna<ElementoBitacora>[] = [
  { clave: "fecha", titulo: "Fecha (hora CR)", celda: (e) => <FechaCR valor={e.fecha} /> },
  {
    clave: "actor",
    titulo: "Actor",
    celda: (e) => e.actor ?? <span className="nota-tecnica">No registrado</span>,
  },
  {
    clave: "accion",
    titulo: "Acción",
    celda: (e) => <Insignia tipo="estado" valor={valorDeInsignia(e.accion)} texto={rotuloDeAccion(e.accion)} />,
  },
  { clave: "objeto", titulo: "Objeto", celda: (e) => <Objeto valor={e.objeto} /> },
  { clave: "detalle", titulo: "Detalle", celda: (e) => e.detalle },
  { clave: "origen", titulo: "Origen", celda: (e) => e.origen },
];

function parametrosDe(filtros: FiltrosBitacora, desplazamiento: number): Record<string, string> {
  const salida: Record<string, string> = {};
  for (const [campo, valor] of Object.entries(filtros)) {
    if (valor) salida[campo] = valor;
  }
  if (desplazamiento > 0) salida.desplazamiento = String(desplazamiento);
  return salida;
}

/**
 * Bitacora de auditoria (T-84, solo administrador): quien hizo que y cuando, de las mas
 * recientes a las mas antiguas. Lee GET /api/v1/bitacora, que une lo que cada coleccion ya
 * registra (no hay coleccion de bitacora). Los filtros viven en la URL, y el CSV usa los
 * mismos. Es una consulta de administracion: recorre varias colecciones y no se refresca sola.
 */
export function Bitacora() {
  const [parametros, fijarParametros] = useSearchParams();
  const filtros = leerFiltrosBitacora(parametros);
  const desplazamiento = Math.max(0, Number(parametros.get("desplazamiento")) || 0);
  const errores = validarFiltrosBitacora(filtros);
  const [borrador, setBorrador] = useState<FiltrosBitacora>(filtros);

  const consultaApi = { ...parametrosDeApiBitacora(filtros), limite: String(LIMITE), desplazamiento: String(desplazamiento) };
  const consulta = useQuery({
    queryKey: ["bitacora", consultaApi],
    queryFn: () => pedir<Pagina<ElementoBitacora>>(RUTA_API, { consulta: consultaApi }),
    enabled: errores.length === 0,
  });
  const pagina = consulta.data;
  const csv = `${RUTA_API}/exportar.csv?${new URLSearchParams(parametrosDeApiBitacora(filtros)).toString()}`;

  function cambiar(campo: keyof FiltrosBitacora, valor: string) {
    setBorrador({ ...borrador, [campo]: valor });
  }

  return (
    <MarcoPagina id="bitacora" ancho="ancho">
      <EncabezadoPagina
        id="bitacora"
        titulo="Bitácora de auditoría"
        migas={[{ rotulo: "Administración", a: "/administracion/salud" }, { rotulo: "Bitácora" }]}
        descripcion="Quién hizo qué y cuándo, de lo que ya registran las alertas, las reglas, las listas de riesgo, los casos, las notificaciones y los respaldos. Un actor vacío significa que la fuente no guarda quién (ediciones de reglas, listas) o que lo hizo el sistema."
      />

      <section className="tarjeta" aria-label="Filtros de la bitácora">
        <h2 className="seccion-titulo">Filtros</h2>
        {errores.length > 0 ? (
          <VistaError
            error={{ texto: "La bitácora tiene parámetros inválidos:", detalles: errores.map((e) => `${e.campo}: ${e.mensaje}`) }}
          />
        ) : null}
        <FiltroBarra
          rotulo="Bitácora de auditoría"
          alBuscar={() => fijarParametros(parametrosDe(borrador, 0))}
          alLimpiar={() => {
            setBorrador(filtrosBitacoraVacios());
            fijarParametros({});
          }}
        >
          <FormularioCampo etiqueta="Fecha desde">
            <input type="date" value={borrador.desde} onChange={(e) => cambiar("desde", e.target.value)} />
          </FormularioCampo>
          <FormularioCampo etiqueta="Fecha hasta">
            <input type="date" value={borrador.hasta} onChange={(e) => cambiar("hasta", e.target.value)} />
          </FormularioCampo>
          <FormularioCampo etiqueta="Agente">
            <input
              type="text"
              placeholder="AGT-001"
              maxLength={32}
              value={borrador.actor}
              onChange={(e) => cambiar("actor", e.target.value)}
            />
          </FormularioCampo>
          <FormularioCampo etiqueta="Tipo de acción">
            <select value={borrador.accion} onChange={(e) => cambiar("accion", e.target.value)}>
              <option value="">Todas</option>
              {TIPOS_ACCION.map(([valor, rotulo]) => (
                <option key={valor} value={valor}>
                  {rotulo}
                </option>
              ))}
            </select>
          </FormularioCampo>
          <FormularioCampo etiqueta="Objeto (prefijo)" ayuda="ALR-, REG-, POL-, CAS-, RSP- o una cuenta">
            <input
              type="text"
              placeholder="ALR-000123"
              maxLength={32}
              value={borrador.objeto}
              onChange={(e) => cambiar("objeto", e.target.value)}
            />
          </FormularioCampo>
        </FiltroBarra>
      </section>

      <section className="tarjeta" aria-label="Resultados de la bitácora">
        <div className="titulo-pantalla resultados-cabecera">
          <strong id="bitacora-total">{pagina ? `${pagina.total} registros` : ""}</strong>
          {pagina && pagina.elementos.length > 0 ? (
            <div className="grupo-botones">
              <a className="boton boton-secundario" href={csv} download>
                Exportar CSV
              </a>
            </div>
          ) : null}
        </div>
        {pagina && pagina.elementos.length > 0 ? (
          <p className="nota-tecnica">
            El CSV aplica estos mismos filtros, con fechas en hora de Costa Rica, hasta {LIMITE_EXPORTACION.toLocaleString("es-CR")} filas.
          </p>
        ) : null}
        <TablaDatos
          leyenda="Bitácora de auditoría"
          columnas={COLUMNAS}
          filas={pagina?.elementos ?? []}
          clave={(e) => `${e.fecha}|${e.objeto}|${e.accion}|${e.actor ?? ""}|${e.detalle}`}
          estado={errores.length > 0 ? "listo" : consulta.isPending ? "cargando" : consulta.isError ? "error" : "listo"}
          error={consulta.isError ? mensajeDeError(consulta.error, "La bitácora", false) : undefined}
          alReintentar={() => void consulta.refetch()}
          vacia={<VistaVacia titulo="No hay registros con esos filtros" texto="Amplíe el rango de fechas o quite algún filtro." />}
          {...(pagina ? { paginacion: { desplazamiento: pagina.desplazamiento, limite: pagina.limite, total: pagina.total } } : {})}
          alCambiarPagina={(nuevo) => fijarParametros(parametrosDe(filtros, nuevo))}
        />
      </section>
    </MarcoPagina>
  );
}
