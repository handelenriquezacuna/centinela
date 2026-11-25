import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";

import { pedir } from "../api/cliente";
import type { RespuestaTablero, ResumenTablero } from "../api/tipos";
import { Cargando } from "../componentes/Cargando";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { TarjetaMetrica } from "../componentes/TarjetaMetrica";
import { VistaError } from "../componentes/VistaError";
import { VistaVacia } from "../componentes/VistaVacia";
import { etiqueta } from "../formatos";
import { Efectividad } from "../tablero/Efectividad";
import { BarrasHorizontales, ColumnasPorHora } from "../tablero/Graficos";
import { Kpis } from "../tablero/Kpis";
import { Variacion } from "../tablero/Variacion";

const DIAS_POR_OMISION = 7;
const DIAS_MAXIMOS = 30;

function leerDias(crudo: string | null): number {
  const dias = Number(crudo);
  return Number.isInteger(dias) && dias >= 1 && dias <= DIAS_MAXIMOS ? dias : DIAS_POR_OMISION;
}

/**
 * Tablero del supervisor (H-20): lee GET /api/v1/tablero, que ya trae las sumas, las
 * comparaciones y la geometria de los graficos. La SPA no recalcula nada.
 */
export function Tablero() {
  const [parametros] = useSearchParams();
  const dias = leerDias(parametros.get("dias"));
  const consulta = useQuery({
    queryKey: ["tablero", dias],
    queryFn: () => pedir<RespuestaTablero>("/api/v1/tablero", { consulta: { dias } }),
  });

  return (
    <>
      {/* Los estilos de los graficos son los mismos de la interfaz HTMX. */}
      <link rel="stylesheet" href="/estaticos/tablero.css" precedence="default" />
      <MarcoPagina id="tablero" ancho="ancho">
        <EncabezadoPagina
          id="tablero"
          titulo="Tablero de indicadores"
          descripcion="Alertas por estado, severidad, agente y hora, contra el periodo anterior."
        />
        {consulta.isPending ? <Cargando texto="Cargando el tablero…" /> : null}
        {consulta.isError ? (
          <VistaError
            error={{ texto: `No se pudo leer el tablero: ${consulta.error.message}`, detalles: [] }}
            alReintentar={() => void consulta.refetch()}
          />
        ) : null}
        {consulta.data ? <Contenido datos={consulta.data} dias={dias} /> : null}
        <Efectividad />
      </MarcoPagina>
    </>
  );
}

export function Contenido({ datos, dias }: { datos: RespuestaTablero; dias: number }) {
  const resumen = datos.resumen;
  if (resumen.vacio) {
    return (
      <section className="tarjeta vacio" id="tablero-vacio" aria-labelledby="titulo-vacio">
        <h2 id="titulo-vacio" className="seccion-titulo">
          Sin indicadores
        </h2>
        <VistaVacia
          titulo="Todavía no hay indicadores calculados."
          texto={
            <>
              El tablero lee cifras ya calculadas y no las arma al vuelo. Para calcularlas: <code>{datos.comando_recalculo}</code>
            </>
          }
        />
      </section>
    );
  }
  return (
    <>
      <nav className="periodos" aria-label="Periodo">
        {datos.periodos.map((p) => (
          <Link key={p} to={`/tablero?dias=${p}`} aria-current={p === dias ? "true" : undefined}>
            {p} {p === 1 ? "día" : "días"}
          </Link>
        ))}
      </nav>
      <p className="nota-tecnica" id="marca-calculo">
        {resumen.calculado_en !== null ? (
          <>
            Último cálculo: <strong><FechaCR valor={resumen.calculado_en} /></strong> (hora CR).
          </>
        ) : (
          "No hay registro de cuándo se calcularon las cifras."
        )}{" "}
        Periodo: {resumen.periodo_actual.desde} a {resumen.periodo_actual.hasta} ({resumen.actual.dias_con_datos} con datos); anterior:{" "}
        {resumen.periodo_previo.desde} a {resumen.periodo_previo.hasta} ({resumen.previo.dias_con_datos} con datos).
      </p>
      {!resumen.hay_periodo_previo ? (
        <p className="nota-tecnica" id="sin-periodo-previo">
          Todavía no hay un periodo anterior con el que comparar; las variaciones aparecerán cuando lo haya.
        </p>
      ) : null}
      <Kpis resumen={resumen} comando={datos.comando_recalculo} />
      <TotalAlertas resumen={resumen} />
      {datos.graficos ? (
        <div className="rejilla-graficos">
          <section className="tarjeta" aria-labelledby="g-estado">
            <h2 id="g-estado" className="seccion-titulo">
              Por estado
            </h2>
            <BarrasHorizontales grafico={datos.graficos.estado} titulo="Alertas por estado" />
          </section>
          <section className="tarjeta" aria-labelledby="g-severidad">
            <h2 id="g-severidad" className="seccion-titulo">
              Por severidad
            </h2>
            <BarrasHorizontales grafico={datos.graficos.severidad} titulo="Alertas por severidad" />
          </section>
          <section className="tarjeta" id="seccion-agente" aria-labelledby="g-agente">
            <h2 id="g-agente" className="seccion-titulo">
              Por agente
            </h2>
            {datos.graficos.agente ? (
              <BarrasHorizontales grafico={datos.graficos.agente} titulo="Alertas por agente" />
            ) : (
              <p className="nota-tecnica">Todavía no hay desglose por agente en las cifras calculadas.</p>
            )}
          </section>
          <section className="tarjeta" id="seccion-hora" aria-labelledby="g-hora">
            <h2 id="g-hora" className="seccion-titulo">
              Por hora (hora CR)
            </h2>
            {datos.graficos.hora ? (
              <ColumnasPorHora grafico={datos.graficos.hora} titulo="Alertas por hora" />
            ) : (
              <p className="nota-tecnica">Todavía no hay desglose por hora en las cifras calculadas.</p>
            )}
          </section>
        </div>
      ) : null}
      <Comparacion resumen={resumen} />
    </>
  );
}

function TotalAlertas({ resumen }: { resumen: ResumenTablero }) {
  const total = resumen.total;
  return (
    <section className="tarjeta" aria-labelledby="titulo-total">
      <h2 id="titulo-total" className="seccion-titulo">
        Total de alertas
      </h2>
      <div className="metricas">
        <TarjetaMetrica
          rotulo="Alertas en el periodo"
          valor={<span id="total-actual">{total.actual}</span>}
          nota={
            <>
              periodo anterior: <strong id="total-previo">{total.previo}</strong>{" "}
              <span id="total-variacion">
                <Variacion valor={total} hayPrevio={resumen.hay_periodo_previo} />
              </span>
            </>
          }
        />
      </div>
    </section>
  );
}

function Comparacion({ resumen }: { resumen: ResumenTablero }) {
  const filas = [
    ...Object.entries(resumen.severidad).map(([clave, v]) => ({ rotulo: `Severidad ${etiqueta(clave)}`, v })),
    ...Object.entries(resumen.estado).map(([clave, v]) => ({ rotulo: `Estado ${etiqueta(clave)}`, v })),
  ];
  return (
    <section className="tarjeta" aria-labelledby="titulo-comparacion">
      <h2 id="titulo-comparacion" className="seccion-titulo">
        Comparación con el periodo anterior
      </h2>
      <table className="tabla-simple" id="tabla-comparacion">
        <caption className="visualmente-oculto">Alertas por severidad y estado, periodo actual y anterior</caption>
        <thead>
          <tr>
            <th scope="col">Indicador</th>
            <th scope="col">Actual</th>
            <th scope="col">Anterior</th>
            <th scope="col">Variación</th>
          </tr>
        </thead>
        <tbody>
          {filas.map(({ rotulo, v }) => (
            <tr key={rotulo}>
              <th scope="row">{rotulo}</th>
              <td>{v.actual}</td>
              <td>{v.previo}</td>
              <td>{resumen.hay_periodo_previo ? <Variacion valor={v} hayPrevio breve /> : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
