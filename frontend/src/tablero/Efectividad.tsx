import { useQuery } from "@tanstack/react-query";

import { pedir } from "../api/cliente";
import type { ReporteEfectividad } from "../api/tipos";
import { Cargando } from "../componentes/Cargando";
import { TarjetaMetrica } from "../componentes/TarjetaMetrica";
import { VistaError } from "../componentes/VistaError";
import { VistaVacia } from "../componentes/VistaVacia";
import { porcentaje } from "../formatos";

/** Una fraccion 0..1 como porcentaje; sin denominador, "sin dato" (no 0 ni 100). */
function razon(valor: number | null): string {
  return valor === null ? "sin dato" : porcentaje(valor * 100, 1);
}

export function Efectividad() {
  const consulta = useQuery({ queryKey: ["efectividad"], queryFn: () => pedir<ReporteEfectividad>("/api/v1/efectividad") });

  return (
    <section className="tarjeta" aria-labelledby="titulo-efectividad" id="efectividad">
      <h2 id="titulo-efectividad" className="seccion-titulo">
        Efectividad del motor
      </h2>
      {consulta.isPending ? <Cargando texto="Cargando la efectividad…" /> : null}
      {consulta.isError ? (
        <VistaError
          error={{ texto: `No se pudo leer la efectividad: ${consulta.error.message}`, detalles: [] }}
          alReintentar={() => void consulta.refetch()}
        />
      ) : null}
      {consulta.data ? <Reporte reporte={consulta.data} /> : null}
    </section>
  );
}

export function Reporte({ reporte }: { reporte: ReporteEfectividad }) {
  if (reporte.transacciones_etiquetadas === 0) {
    return (
      <div id="efectividad-sin-datos">
        <VistaVacia
          titulo="No hay transacciones etiquetadas con que medir."
          texto={`Las etiquetas las pone el generador de datos de prueba. Alertas sin etiqueta: ${reporte.alertas_sin_etiqueta}.`}
        />
      </div>
    );
  }
  return (
    <>
      <div className="metricas">
        <TarjetaMetrica id="efectividad-precision" rotulo="Precisión" valor={razon(reporte.precision)} estado={reporte.precision === null ? "sin_datos" : "normal"} />
        <TarjetaMetrica id="efectividad-exhaustividad" rotulo="Exhaustividad" valor={razon(reporte.exhaustividad)} estado={reporte.exhaustividad === null ? "sin_datos" : "normal"} />
        <TarjetaMetrica rotulo={`Fraudes detectados de ${reporte.fraudes}`} valor={reporte.detectados} />
        <TarjetaMetrica rotulo={`Falsos positivos de ${reporte.legitimas} legítimas`} valor={reporte.falsos_positivos} />
      </div>
      <table className="tabla-simple" id="tabla-efectividad">
        <caption className="visualmente-oculto">Exhaustividad por patrón de fraude</caption>
        <thead>
          <tr>
            <th scope="col">Patrón</th>
            <th scope="col">Fraudes</th>
            <th scope="col">Detectados</th>
            <th scope="col">Perdidos</th>
            <th scope="col">Exhaustividad</th>
            <th scope="col">Episodios detectados</th>
          </tr>
        </thead>
        <tbody>
          {reporte.por_patron.map((p) => (
            <tr key={p.patron}>
              <th scope="row">{p.patron}</th>
              <td>{p.fraudes}</td>
              <td>{p.detectados}</td>
              <td>{p.perdidos}</td>
              <td>{razon(p.exhaustividad)}</td>
              <td>
                {p.episodios_detectados} de {p.episodios}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
