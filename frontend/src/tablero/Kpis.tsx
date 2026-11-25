import type { ResumenTablero } from "../api/tipos";
import { FechaCR } from "../componentes/FechaCR";
import { MontoColones } from "../componentes/MontoColones";
import { TarjetaMetrica } from "../componentes/TarjetaMetrica";
import { VistaVacia } from "../componentes/VistaVacia";
import { colones, decimal } from "../formatos";
import { tiempoCorto } from "./formatos";
import { Variacion } from "./Variacion";

interface Propiedades {
  resumen: ResumenTablero;
  comando: string;
}

/** Los cuatro indicadores del supervisor (T-51). Sin dato se dice, no se inventa un cero. */
export function Kpis({ resumen, comando }: Propiedades) {
  const k = resumen.kpi;
  const hayPrevio = resumen.hay_periodo_previo;
  const sinResueltas = "Todavía no hay alertas resueltas en el periodo.";
  return (
    <section className="tarjeta" aria-labelledby="titulo-kpis" id="kpis">
      <h2 id="titulo-kpis" className="seccion-titulo">
        Indicadores del supervisor
      </h2>
      {!k.disponible ? (
        <div id="kpis-sin-dato">
          <VistaVacia
            titulo="Los indicadores todavía no están calculados."
            texto={
              <>
                Para calcularlos: <code>{comando}</code>
              </>
            }
          />
        </div>
      ) : (
        <div className="metricas">
          <TarjetaMetrica
            id="kpi-tiempo"
            rotulo="Tiempo hasta la decisión (promedio)"
            estado={k.tiempo_decision_s.actual === null ? "sin_datos" : "normal"}
            valor={k.tiempo_decision_s.actual === null ? "" : tiempoCorto(k.tiempo_decision_s.actual)}
            nota={
              k.tiempo_decision_s.actual === null ? (
                sinResueltas
              ) : (
                <>
                  {k.tiempo_decision_s.previo !== null ? `anterior: ${tiempoCorto(k.tiempo_decision_s.previo)}. ` : ""}
                  <Variacion valor={k.tiempo_decision_s} hayPrevio={hayPrevio} unidad=" s" />
                </>
              )
            }
          />
          <TarjetaMetrica
            id="kpi-monto"
            rotulo="Monto protegido (confirmadas y escaladas)"
            valor={<MontoColones valor={k.monto_protegido_crc.actual} />}
            nota={
              <>
                {k.monto_protegido_crc.previo !== null ? `anterior: ${colones(k.monto_protegido_crc.previo)}. ` : ""}
                <Variacion valor={k.monto_protegido_crc} hayPrevio={hayPrevio} comoColones />
              </>
            }
          />
          <TarjetaMetrica
            id="kpi-tasa"
            rotulo="Tasa de confirmación"
            estado={k.tasa_confirmacion.actual === null ? "sin_datos" : "normal"}
            valor={k.tasa_confirmacion.actual === null ? "" : `${decimal(k.tasa_confirmacion.actual, 1)}%`}
            nota={
              k.tasa_confirmacion.actual === null ? (
                sinResueltas
              ) : (
                <>
                  {k.tasa_confirmacion.previo !== null ? `anterior: ${decimal(k.tasa_confirmacion.previo, 1)}%. ` : ""}
                  <Variacion valor={k.tasa_confirmacion} hayPrevio={hayPrevio} unidad=" pts" />
                </>
              )
            }
          />
          <TarjetaMetrica
            id="kpi-pendientes"
            rotulo="Pendientes (nueva o en revisión)"
            valor={k.pendientes.actual}
            nota={
              <>
                {k.pendiente_mas_antigua !== null ? (
                  <>
                    la más antigua sin atender: <FechaCR valor={k.pendiente_mas_antigua} formato="con_zona" />, hace {decimal(k.antiguedad_horas, 1)} h.{" "}
                  </>
                ) : (
                  "ninguna pendiente. "
                )}
                <Variacion valor={k.pendientes} hayPrevio={hayPrevio} />
              </>
            }
          />
        </div>
      )}
    </section>
  );
}
