import type { ResumenTablero } from "../api/tipos";
import { colones, decimal, horaCostaRica } from "../formatos";
import { tiempoCorto } from "./formatos";
import { Variacion } from "./Variacion";

interface Propiedades {
  resumen: ResumenTablero;
  comando: string;
}

/** Los cuatro indicadores del supervisor (T-51). Sin dato en el rollup se dice, no se inventa un cero. */
export function Kpis({ resumen, comando }: Propiedades) {
  const k = resumen.kpi;
  const hayPrevio = resumen.hay_periodo_previo;
  return (
    <section className="tarjeta" aria-labelledby="titulo-kpis" id="kpis">
      <h2 id="titulo-kpis" className="seccion-titulo">
        Indicadores del supervisor
      </h2>
      {!k.disponible ? (
        <p className="nota-tecnica" id="kpis-sin-dato">
          El rollup todavía no trae estos indicadores. Para calcularlos: <code>{comando}</code>
        </p>
      ) : (
        <div className="cifras">
          <div id="kpi-tiempo">
            <span className="cifra-grande">
              {k.tiempo_decision_s.actual === null ? "sin resueltas" : tiempoCorto(k.tiempo_decision_s.actual)}
            </span>
            <div>Tiempo hasta la decisión (promedio)</div>
            <div className="nota-tecnica">
              {k.tiempo_decision_s.previo !== null ? `anterior: ${tiempoCorto(k.tiempo_decision_s.previo)}. ` : ""}
              <Variacion valor={k.tiempo_decision_s} hayPrevio={hayPrevio} unidad=" s" />
            </div>
          </div>
          <div id="kpi-monto">
            <span className="cifra-grande">{colones(k.monto_protegido_crc.actual)}</span>
            <div>Monto protegido (confirmadas y escaladas)</div>
            <div className="nota-tecnica">
              {k.monto_protegido_crc.previo !== null ? `anterior: ${colones(k.monto_protegido_crc.previo)}. ` : ""}
              <Variacion valor={k.monto_protegido_crc} hayPrevio={hayPrevio} comoColones />
            </div>
          </div>
          <div id="kpi-tasa">
            <span className="cifra-grande">
              {k.tasa_confirmacion.actual === null ? "sin resueltas" : `${decimal(k.tasa_confirmacion.actual, 1)}%`}
            </span>
            <div>Tasa de confirmación</div>
            <div className="nota-tecnica">
              {k.tasa_confirmacion.previo !== null ? `anterior: ${decimal(k.tasa_confirmacion.previo, 1)}%. ` : ""}
              <Variacion valor={k.tasa_confirmacion} hayPrevio={hayPrevio} unidad=" pts" />
            </div>
          </div>
          <div id="kpi-pendientes">
            <span className="cifra-grande">{k.pendientes.actual}</span>
            <div>Pendientes (nueva o en revisión)</div>
            <div className="nota-tecnica">
              {k.pendiente_mas_antigua !== null
                ? `la más antigua sin atender: ${horaCostaRica(k.pendiente_mas_antigua)} (hora CR), hace ${decimal(k.antiguedad_horas, 1)} h. `
                : "ninguna pendiente. "}
              <Variacion valor={k.pendientes} hayPrevio={hayPrevio} />
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
