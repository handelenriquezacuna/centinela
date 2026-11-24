import { render, screen, within } from "@testing-library/react";

import type { ResumenTablero } from "../api/tipos";
import { Kpis } from "./Kpis";
import { TABLERO_CON_DATOS } from "./tablero.datos";

const RESUMEN = TABLERO_CON_DATOS.resumen as ResumenTablero;

describe("KPIs del supervisor", () => {
  it("muestra cada cifra con su formato de Costa Rica y su comparacion en palabras", () => {
    render(<Kpis resumen={RESUMEN} comando="python tareas.py indicadores" />);

    const tiempo = document.getElementById("kpi-tiempo") as HTMLElement;
    expect(within(tiempo).getByText("1 h 30 min")).toBeInTheDocument();
    expect(tiempo).toHaveTextContent("anterior: 2 h 0 min");
    expect(tiempo).toHaveTextContent("-1800 s (-25,0%) contra el periodo anterior");

    const monto = document.getElementById("kpi-monto") as HTMLElement;
    expect(within(monto).getByText("₡2.500.000")).toBeInTheDocument();
    expect(monto).toHaveTextContent("anterior: ₡1.000.000");
    expect(monto).toHaveTextContent("+₡1.500.000 (150,0%)");

    const tasa = document.getElementById("kpi-tasa") as HTMLElement;
    expect(within(tasa).getByText("80,0%")).toBeInTheDocument();

    const pendientes = document.getElementById("kpi-pendientes") as HTMLElement;
    expect(within(pendientes).getByText("4")).toBeInTheDocument();
    expect(pendientes).toHaveTextContent("la más antigua sin atender: 20/11/2026 06:00 (hora CR), hace 78,5 h");
  });

  it("una bajada y una subida llevan su clase y tambien su signo", () => {
    render(<Kpis resumen={RESUMEN} comando="x" />);
    expect(screen.getByText("-1800 s (-25,0%)")).toHaveClass("variacion-baja");
    expect(screen.getByText("+2 (100,0%)")).toHaveClass("variacion-sube");
  });

  it("sin los campos nuevos en el rollup lo dice y da el comando, sin inventar ceros", () => {
    const sin: ResumenTablero = { ...RESUMEN, kpi: { ...RESUMEN.kpi, disponible: false } };
    render(<Kpis resumen={sin} comando="python tareas.py indicadores" />);
    expect(document.getElementById("kpis-sin-dato")).toHaveTextContent("python tareas.py indicadores");
    expect(document.getElementById("kpi-monto")).toBeNull();
  });

  it("sin alertas resueltas dice 'sin resueltas', no cero", () => {
    const nulo = { actual: null, previo: null, delta: null, porcentaje: null };
    const sin: ResumenTablero = { ...RESUMEN, kpi: { ...RESUMEN.kpi, tiempo_decision_s: nulo, tasa_confirmacion: nulo } };
    render(<Kpis resumen={sin} comando="x" />);
    expect(screen.getAllByText("sin resueltas")).toHaveLength(2);
  });
});
