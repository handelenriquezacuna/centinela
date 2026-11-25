import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";

import type { ElementoTransaccion } from "../api/tipos";
import { instalarApiFalsa } from "../pruebas/apiFalsa";
import { Busqueda } from "./Busqueda";

function transaccion(numero: number, campos: Partial<ElementoTransaccion> = {}): ElementoTransaccion {
  return {
    transaccion_id: `TXN-${String(numero).padStart(3, "0")}`,
    cuenta_origen: "CTA-001",
    sinpe_origen: "8712-4455",
    cuenta_destino: "6033-9001",
    monto_crc: 750_000,
    moneda: "CRC",
    canal: "sinpe_movil",
    fecha: "2026-10-15T04:47:00Z",
    alerta_id: null,
    alerta_estado: null,
    ...campos,
  };
}

function pagina(elementos: ElementoTransaccion[], total = elementos.length, campos: object = {}) {
  return { cuerpo: { total, limite: 25, desplazamiento: 0, elementos, plan: null, ...campos } };
}

function montar(ruta = "/busqueda") {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter initialEntries={[ruta]}>
        <Routes>
          <Route path="/busqueda" element={<Busqueda />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Busqueda forense", () => {
  it("muestra los resultados en una tabla accesible con colones enteros y hora de Costa Rica", async () => {
    instalarApiFalsa({
      "GET /api/v1/transacciones": pagina([transaccion(1), transaccion(2, { monto_crc: 280_000, alerta_id: "ALR-002", alerta_estado: "en_revision" })]),
    });
    montar();

    const tabla = await screen.findByRole("table", { name: "Transacciones que cumplen los filtros" });
    expect(within(tabla).getAllByRole("columnheader").map((c) => c.textContent)).toEqual([
      "Transacción",
      "Fecha y hora (CR)",
      "Cuenta origen",
      "Cuenta destino",
      "Monto",
      "Canal",
      "Alerta relacionada",
    ]);
    expect(within(tabla).getByText("₡750.000")).toBeInTheDocument();
    expect(within(tabla).getAllByText("14/10/2026 22:47")).toHaveLength(2);
    expect(within(tabla).getAllByText("SINPE Móvil")).toHaveLength(2);
    expect(within(tabla).getByText("Sin alerta")).toBeInTheDocument();
    expect(within(tabla).getByRole("link", { name: "ALR-002 · En revisión" })).toHaveAttribute("href", "/detalle/ALR-002");
    expect(screen.getByText("2 resultados")).toBeInTheDocument();
  });

  it("aplica los filtros como parametros de la API, con las fechas como dias de Costa Rica", async () => {
    const usuario = userEvent.setup();
    const api = instalarApiFalsa({ "GET /api/v1/transacciones": pagina([transaccion(1)]) });
    montar();
    await screen.findByRole("table");

    await usuario.type(screen.getByLabelText("Fecha desde"), "2026-10-01");
    await usuario.type(screen.getByLabelText("Fecha hasta"), "2026-10-05");
    await usuario.type(screen.getByLabelText(/Monto mínimo/), "30000");
    await usuario.type(screen.getByLabelText(/Cuenta \(origen/), "CTA-002");
    await usuario.selectOptions(screen.getByLabelText("Canal"), "sinpe_movil");
    await usuario.selectOptions(screen.getByLabelText(/Estado de la alerta/), "nueva");
    await usuario.selectOptions(screen.getByLabelText("Resultados por página"), "10");
    await usuario.click(screen.getByRole("button", { name: "Buscar" }));

    await waitFor(() => expect(api.pedidos.length).toBe(2));
    const parametros = new URLSearchParams(api.pedidos[1]?.ruta.split("?")[1]);
    expect(Object.fromEntries(parametros)).toEqual({
      desde: "2026-10-01T00:00:00-06:00",
      hasta: "2026-10-05T23:59:59.999999-06:00",
      monto_min: "30000",
      cuenta: "CTA-002",
      canal: "sinpe_movil",
      estado_alerta: "nueva",
      limite: "10",
      desplazamiento: "0",
    });
  });

  it("el CSV usa exactamente los mismos filtros, bajo /api/v1", async () => {
    instalarApiFalsa({ "GET /api/v1/transacciones": pagina([transaccion(1)]) });
    montar("/busqueda?cuenta=CTA-002&monto_min=30000&desde=2026-10-01&limite=25");

    const enlace = await screen.findByRole("link", { name: "Exportar CSV" });
    const url = new URL(enlace.getAttribute("href") ?? "", "http://localhost");
    expect(url.pathname).toBe("/api/v1/transacciones/exportar.csv");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      cuenta: "CTA-002",
      monto_min: "30000",
      desde: "2026-10-01T00:00:00-06:00",
    });
    expect(enlace).toHaveAttribute("download");
  });

  it("un monto con decimales no llega a la API: se avisa en el formulario", async () => {
    const api = instalarApiFalsa({ "GET /api/v1/transacciones": pagina([]) });
    montar("/busqueda?monto_max=9.5");
    const aviso = await screen.findByRole("alert");
    expect(aviso).toHaveTextContent("monto_max: debe ser un entero en colones, sin decimales");
    expect(api.pedidos).toHaveLength(0);
  });

  it("muestra el 422 de la API en la forma unica del error", async () => {
    instalarApiFalsa({
      "GET /api/v1/transacciones": { estado: 422, cuerpo: { error: { codigo: "parametros_invalidos", mensaje: "La busqueda tiene parametros invalidos", detalles: [{ campo: "cuenta", mensaje: "cuenta desconocida" }] } } },
    });
    montar("/busqueda?cuenta=XX");
    expect(await screen.findByText("La busqueda tiene parametros invalidos")).toBeInTheDocument();
    expect(screen.getByText("cuenta: cuenta desconocida")).toBeInTheDocument();
  });

  it("pagina con botones que conservan los filtros y respetan el limite", async () => {
    const usuario = userEvent.setup();
    const api = instalarApiFalsa({ "GET /api/v1/transacciones": pagina([transaccion(1), transaccion(2)], 30, { limite: 10 }) });
    montar("/busqueda?cuenta=CTA-001&limite=10");

    const siguiente = await screen.findByRole("button", { name: "Siguiente" });
    expect(screen.getByRole("button", { name: "Anterior" })).toBeDisabled();
    expect(screen.getByText("1 a 10 de 30")).toBeInTheDocument();

    await usuario.click(siguiente);

    await waitFor(() => expect(api.pedidos.length).toBe(2));
    const parametros = new URLSearchParams(api.pedidos[1]?.ruta.split("?")[1]);
    expect(Object.fromEntries(parametros)).toMatchObject({ cuenta: "CTA-001", limite: "10", desplazamiento: "10" });
  });

  it("sin coincidencias no ofrece exportar", async () => {
    instalarApiFalsa({ "GET /api/v1/transacciones": pagina([]) });
    montar("/busqueda?cuenta=CTA-404");
    expect(await screen.findByText("No hay transacciones que cumplan los filtros.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Exportar CSV" })).not.toBeInTheDocument();
  });

  it("muestra el plan de ejecucion cuando se pide", async () => {
    instalarApiFalsa({
      "GET /api/v1/transacciones": pagina([transaccion(1)], 1, {
        plan: { etapas: ["LIMIT", "FETCH", "IXSCAN"], indices: ["fecha_-1"], documentos_examinados: 1, claves_examinadas: 1, milisegundos: 2 },
      }),
    });
    montar("/busqueda?depurar=on");
    expect(await screen.findByText(/Plan de ejecución/)).toHaveTextContent("LIMIT > FETCH > IXSCAN");
  });
});
