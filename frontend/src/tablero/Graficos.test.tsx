import { render, screen } from "@testing-library/react";

import { BarrasHorizontales, ColumnasPorHora } from "./Graficos";
import { TABLERO_CON_DATOS } from "./tablero.datos";

const GRAFICOS = TABLERO_CON_DATOS.graficos!;

describe("graficos SVG del tablero", () => {
  it("dibuja la geometria del servidor sin recalcularla y la describe en texto", () => {
    const { container } = render(<BarrasHorizontales grafico={GRAFICOS.estado} titulo="Alertas por estado" />);

    const svg = screen.getByRole("img", { name: "Alertas por estado: Nueva 3, En revisión 1, Confirmada 4" });
    expect(svg).toHaveAttribute("viewBox", "0 0 430 130");
    const barras = container.querySelectorAll("rect");
    expect([...barras].map((b) => b.getAttribute("width"))).toEqual(["195", "65", "260"]);
    expect(barras[0]).toHaveClass("barra-nueva");
    expect(barras[0]).toHaveAttribute("x", "120");
    expect(screen.getByText("En revisión")).toBeInTheDocument();
  });

  it("las columnas por hora rotulan cada tres horas y cada una trae su valor", () => {
    const { container } = render(<ColumnasPorHora grafico={GRAFICOS.hora!} titulo="Alertas por hora" />);

    expect(screen.getByRole("img", { name: "Alertas por hora: 09:00 2, 22:00 6" })).toBeInTheDocument();
    expect(container.querySelectorAll("rect")).toHaveLength(3);
    expect(container.querySelector("rect > title")).toHaveTextContent("09:00 - 2");
    // 09 y 00 llevan rotulo (multiplos de 3); 22 no.
    expect([...container.querySelectorAll("svg > g > text")].map((t) => t.textContent)).toEqual(["09", "00"]);
  });

  it("sin alertas en ninguna hora la alternativa textual lo dice", () => {
    render(<ColumnasPorHora grafico={{ ...GRAFICOS.hora!, columnas: [{ x: 0, y: 120, alto: 0, hora: "00", valor: 0 }] }} titulo="Alertas por hora" />);
    expect(screen.getByRole("img", { name: "Alertas por hora: sin alertas" })).toBeInTheDocument();
  });
});
