import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { instalarApiFalsa } from "../pruebas/apiFalsa";
import { TABLERO_CON_DATOS } from "../tablero/tablero.datos";
import { Tablero } from "./Tablero";

const EFECTIVIDAD = {
  transacciones_etiquetadas: 100,
  fraudes: 20,
  legitimas: 80,
  detectados: 18,
  perdidos: 2,
  falsos_positivos: 4,
  verdaderos_negativos: 76,
  alertas_totales: 22,
  alertas_sin_etiqueta: 0,
  precision: 0.8182,
  exhaustividad: 0.9,
  por_patron: [{ patron: "mulas", fraudes: 20, detectados: 18, perdidos: 2, exhaustividad: null, episodios: 5, episodios_detectados: 5 }],
};

function montar(ruta = "/tablero") {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter initialEntries={[ruta]}>
        <Tablero />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Tablero", () => {
  it("pide el periodo de la URL y pinta totales, comparacion, marca de calculo y efectividad", async () => {
    const api = instalarApiFalsa({
      "GET /api/v1/tablero": { cuerpo: TABLERO_CON_DATOS },
      "GET /api/v1/efectividad": { cuerpo: EFECTIVIDAD },
    });
    montar("/tablero?dias=14");

    expect(await screen.findByRole("heading", { name: "Tablero de indicadores" })).toBeInTheDocument();
    expect(await screen.findByText("₡2.500.000")).toBeInTheDocument();
    expect(api.pedidos.some((p) => p.ruta === "/api/v1/tablero?dias=14")).toBe(true);
    expect(document.getElementById("total-actual")).toHaveTextContent("10");
    expect(document.getElementById("total-previo")).toHaveTextContent("8");
    expect(document.getElementById("marca-calculo")).toHaveTextContent("Último cálculo: 23/11/2026 12:30 (hora CR)");

    const periodos = screen.getByRole("navigation", { name: "Periodo" });
    expect(within(periodos).getByRole("link", { name: "7 días" })).toHaveAttribute("href", "/tablero?dias=7");
    expect(within(periodos).getByRole("link", { name: "1 día" })).toBeInTheDocument();

    const tabla = screen.getByRole("table", { name: /severidad y estado/ });
    expect(within(tabla).getByRole("row", { name: /Severidad Crítica 4 2 \+2 \(100,0%\)/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Alertas por agente: AGT-901 5/ })).toBeInTheDocument();

    expect(await screen.findByText("81,8%")).toBeInTheDocument();
    expect(document.getElementById("efectividad-exhaustividad")).toHaveTextContent("90,0%");
    // Sin denominador: "sin dato", no cero.
    expect(within(document.getElementById("tabla-efectividad") as HTMLElement).getByText("sin dato")).toBeInTheDocument();
  });

  it("sin rollup dice que falta y el comando; los graficos sin desglose lo avisan", async () => {
    instalarApiFalsa({
      "GET /api/v1/tablero": { cuerpo: { dias: 7, periodos: [1, 7, 14, 30], comando_recalculo: "python tareas.py indicadores", resumen: { vacio: true, dias: 7 }, graficos: null } },
      "GET /api/v1/efectividad": { cuerpo: { ...EFECTIVIDAD, transacciones_etiquetadas: 0, alertas_sin_etiqueta: 3 } },
    });
    montar();

    expect(await screen.findByRole("heading", { name: "Sin indicadores" })).toBeInTheDocument();
    expect(screen.getByText("python tareas.py indicadores")).toBeInTheDocument();
    expect(await screen.findByText(/No hay transacciones etiquetadas/)).toBeInTheDocument();
  });

  it("un 403 se dice con el mensaje del servidor", async () => {
    instalarApiFalsa({
      "GET /api/v1/tablero": { estado: 403, cuerpo: { error: { codigo: "prohibido", mensaje: "Tu rol no permite esta operación", detalles: [] } } },
      "GET /api/v1/efectividad": { estado: 403, cuerpo: { error: { codigo: "prohibido", mensaje: "Tu rol no permite esta operación", detalles: [] } } },
    });
    montar();
    expect(await screen.findByText("No se pudo leer el tablero: Tu rol no permite esta operación")).toBeInTheDocument();
  });

  it("sin periodo anterior lo dice una sola vez, no en cada cifra", async () => {
    const datos = structuredClone(TABLERO_CON_DATOS) as { resumen: { hay_periodo_previo: boolean } };
    datos.resumen.hay_periodo_previo = false;
    instalarApiFalsa({ "GET /api/v1/tablero": { cuerpo: datos }, "GET /api/v1/efectividad": { cuerpo: EFECTIVIDAD } });
    montar();

    await screen.findByText("₡2.500.000");
    expect(screen.getAllByText(/periodo anterior con el que comparar/)).toHaveLength(1);
    expect(document.body.textContent).not.toMatch(/sin periodo anterior con que comparar/);
  });

  it("no usa la palabra rollup en la pantalla", async () => {
    instalarApiFalsa({
      "GET /api/v1/tablero": { cuerpo: { dias: 7, periodos: [1, 7, 14, 30], comando_recalculo: "python tareas.py indicadores", resumen: { vacio: true, dias: 7 }, graficos: null } },
      "GET /api/v1/efectividad": { cuerpo: { ...EFECTIVIDAD, transacciones_etiquetadas: 0 } },
    });
    montar();
    await screen.findByRole("heading", { name: "Sin indicadores" });
    expect(document.body.textContent?.toLowerCase()).not.toContain("rollup");
  });
});
