import { render, screen, within } from "@testing-library/react";

import { disposicionDelCaso } from "../pruebas/datosRed";
import { Sankey, TablaSaltos } from "./Sankey";

describe("Sankey", () => {
  it("dibuja las cuatro bandas del caso con montos enteros y anchos decrecientes", () => {
    const { container } = render(<Sankey disposicion={disposicionDelCaso()} />);

    const bandas = [...container.querySelectorAll("path.sankey-banda")];
    expect(bandas).toHaveLength(4);
    expect(bandas.map((b) => b.getAttribute("data-monto"))).toEqual(["280000", "276000", "185000", "90000"]);
    const grosores = bandas.map((b) => Number(b.getAttribute("data-grosor")));
    expect(grosores[0]).toBe(140);
    expect(grosores[0]).toBeGreaterThan(grosores[1] ?? 0);
    expect(grosores[1]).toBeGreaterThan(grosores[2] ?? 0);
    expect(grosores[2]).toBeGreaterThan(grosores[3] ?? 0);
    expect(grosores[3]).toBeGreaterThan(0);
  });

  it("rotula cada banda con colones y hora de Costa Rica en su titulo", () => {
    const { container } = render(<Sankey disposicion={disposicionDelCaso()} />);

    const titulo = container.querySelector("path.sankey-banda title");
    expect(titulo).toHaveTextContent("₡280.000 de CLI-001 a 6033-9001, 24/09/2026 22:00 (hora CR)");
    expect(container.querySelectorAll("text.sankey-monto")[2]).toHaveTextContent("₡185.000");
  });

  it("es una imagen con el resumen del servidor como nombre accesible", () => {
    render(<Sankey disposicion={disposicionDelCaso()} />);

    const imagen = screen.getByRole("img", { name: /desde la víctima María Rodríguez Vargas: 4 transferencias/ });
    expect(imagen.tagName.toLowerCase()).toBe("svg");
  });

  it("pone a la victima en la columna 0, sin marca ni trama", () => {
    const { container } = render(<Sankey disposicion={disposicionDelCaso()} />);

    const victima = container.querySelector("g.sankey-nodo[data-victima='si']");
    expect(victima).not.toBeNull();
    expect(victima).toHaveAttribute("data-columna", "0");
    expect(victima).toHaveAttribute("data-en-lista", "no");
    expect(victima).toHaveTextContent("Víctima");
    expect(victima?.querySelector("rect")).not.toHaveAttribute("fill");
  });

  it("marca las cuentas en lista con trama Y con texto, no solo con color", () => {
    const { container } = render(<Sankey disposicion={disposicionDelCaso(["6033-9001"])} />);

    const nodo = container.querySelector("g.sankey-nodo[data-cuenta='6033-9001']");
    expect(nodo).toHaveAttribute("data-en-lista", "si");
    expect(nodo?.querySelector("rect")).toHaveAttribute("fill", "url(#patron-lista)");
    expect(nodo).toHaveTextContent("En lista de riesgo");
    expect(container.querySelectorAll("g.sankey-nodo[data-en-lista='si']")).toHaveLength(1);
    expect(container.querySelector("g.sankey-nodo[data-cuenta='8455-1177']")).toHaveTextContent("Sin marca");
  });

  it("ofrece la tabla de saltos como alternativa en texto", () => {
    render(<TablaSaltos disposicion={disposicionDelCaso()} />);

    const tabla = screen.getByRole("table", { name: "Saltos del recorrido del dinero", hidden: true });
    const filas = within(tabla).getAllByRole("row", { hidden: true });
    expect(filas).toHaveLength(5);
    expect(filas[2]).toHaveTextContent("₡276.000");
    expect(filas[2]).toHaveTextContent("6033-9001");
    expect(filas[2]).toHaveTextContent("7104-2288");
    expect(filas[2]).toHaveTextContent("24/09/2026 22:20");
  });
});
