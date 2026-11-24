import { useQuery } from "@tanstack/react-query";

import { pedir } from "../api/cliente";
import type { ReporteEfectividad } from "../api/tipos";
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
      {consulta.isPending ? <p role="status">Cargando la efectividad…</p> : null}
      {consulta.isError ? (
        <p className="mensaje mensaje-error" role="alert">
          No se pudo leer la efectividad: {consulta.error.message}
        </p>
      ) : null}
      {consulta.data ? <Reporte reporte={consulta.data} /> : null}
    </section>
  );
}

export function Reporte({ reporte }: { reporte: ReporteEfectividad }) {
  if (reporte.transacciones_etiquetadas === 0) {
    return (
      <p className="nota-tecnica" id="efectividad-sin-datos">
        No hay transacciones etiquetadas por el generador con que medir. Alertas sin etiqueta: {reporte.alertas_sin_etiqueta}.
      </p>
    );
  }
  return (
    <>
      <div className="cifras">
        <div id="efectividad-precision">
          <span className="cifra-grande">{razon(reporte.precision)}</span>
          <div>Precisión</div>
        </div>
        <div id="efectividad-exhaustividad">
          <span className="cifra-grande">{razon(reporte.exhaustividad)}</span>
          <div>Exhaustividad</div>
        </div>
        <div>
          <span className="cifra-grande">{reporte.detectados}</span>
          <div>Fraudes detectados de {reporte.fraudes}</div>
        </div>
        <div>
          <span className="cifra-grande">{reporte.falsos_positivos}</span>
          <div>Falsos positivos de {reporte.legitimas} legítimas</div>
        </div>
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
