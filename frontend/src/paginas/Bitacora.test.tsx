import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

import type { ElementoBitacora } from "../api/tipos";
import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { Bitacora } from "./Bitacora";

function fila(cambios: Partial<ElementoBitacora> = {}): ElementoBitacora {
  return {
    // 04:47 UTC es 22:47 en Costa Rica.
    fecha: "2026-11-20T04:47:00Z",
    actor: "AGT-001",
    accion: "alerta_confirmada",
    objeto: "ALR-000001",
    detalle: "en_revision -> confirmada: cliente confirma",
    origen: "alertas",
    ...cambios,
  };
}

function pagina(elementos: ElementoBitacora[], total = elementos.length) {
  return { total, limite: 50, desplazamiento: 0, elementos };
}

function montar(ruta = "/administracion/bitacora") {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter initialEntries={[ruta]}>
        <Bitacora />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Bitacora", () => {
  it("lista las acciones con hora de Costa Rica, actor, insignia, objeto enlazado y origen", async () => {
    instalarApiFalsa({
      "GET /api/v1/bitacora": {
        cuerpo: pagina([
          fila(),
          fila({ actor: null, accion: "regla_editada", objeto: "REG-001", detalle: "regla v1 -> v2: peso 35 -> 30", origen: "reglas_deteccion" }),
        ]),
      },
    });
    montar();

    expect(await screen.findByRole("heading", { name: "Bitácora de auditoría" })).toBeInTheDocument();
    const tabla = await screen.findByRole("table", { name: "Bitácora de auditoría" });
    const primera = within(tabla).getByRole("row", { name: /ALR-000001/ });
    expect(primera).toHaveTextContent("19/11/2026 22:47");
    expect(primera).toHaveTextContent("AGT-001");
    expect(primera).toHaveTextContent("Alerta confirmada");
    expect(within(primera).getByRole("link", { name: "ALR-000001" })).toHaveAttribute("href", "/detalle/ALR-000001");
    const regla = within(tabla).getByRole("row", { name: /REG-001/ });
    expect(regla).toHaveTextContent("No registrado");
    expect(regla).toHaveTextContent("Regla editada");
    expect(within(regla).queryByRole("link")).toBeNull();
    expect(document.getElementById("bitacora-total")).toHaveTextContent("2 registros");
  });

  it("los filtros de la URL viajan a la API y al enlace del CSV", async () => {
    const api = instalarApiFalsa({ "GET /api/v1/bitacora": { cuerpo: pagina([fila()]) } });
    montar("/administracion/bitacora?actor=AGT-001&accion=alerta_confirmada&desde=2026-11-20");

    const enlace = await screen.findByRole("link", { name: "Exportar CSV" });
    const consulta = new URLSearchParams(api.pedidos[0]?.ruta.split("?")[1]);
    expect(consulta.get("actor")).toBe("AGT-001");
    expect(consulta.get("accion")).toBe("alerta_confirmada");
    expect(consulta.get("desde")).toBe("2026-11-20T00:00:00-06:00");
    expect(consulta.get("limite")).toBe("50");
    const csv = new URL(enlace.getAttribute("href") ?? "", "http://localhost");
    expect(csv.pathname).toBe("/api/v1/bitacora/exportar.csv");
    expect(csv.searchParams.get("actor")).toBe("AGT-001");
    expect(csv.searchParams.get("limite")).toBeNull();
  });

  it("Buscar aplica el formulario y vuelve a pedir con el agente elegido", async () => {
    const api = instalarApiFalsa({ "GET /api/v1/bitacora": { cuerpo: pagina([fila()]) } });
    montar();
    await screen.findByRole("table", { name: "Bitácora de auditoría" });

    await userEvent.type(screen.getByLabelText("Agente"), "AGT-002");
    await userEvent.selectOptions(screen.getByLabelText("Tipo de acción"), "alerta_escalada");
    await userEvent.click(screen.getByRole("button", { name: "Buscar" }));

    await waitFor(() => expect(api.pedidos.some((p) => p.ruta.includes("actor=AGT-002") && p.ruta.includes("accion=alerta_escalada"))).toBe(true));
  });

  it("un filtro invalido se avisa y no se pide", async () => {
    const api = instalarApiFalsa({ "GET /api/v1/bitacora": { cuerpo: pagina([]) } });
    montar("/administracion/bitacora?objeto=ALR-.*");

    expect(await screen.findByText(/La bitácora tiene parámetros inválidos/)).toBeInTheDocument();
    expect(api.pedidos).toHaveLength(0);
  });

  it("sin registros dice que se amplie el rango y no ofrece el CSV", async () => {
    instalarApiFalsa({ "GET /api/v1/bitacora": { cuerpo: pagina([]) } });
    montar();

    expect(await screen.findByText("No hay registros con esos filtros")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Exportar CSV" })).toBeNull();
  });

  it("un error del servidor se muestra con reintento", async () => {
    instalarApiFalsa({ "GET /api/v1/bitacora": errorApi(500, "error_interno", "falla") });
    montar();

    expect(await screen.findByRole("button", { name: /Reintentar/ })).toBeInTheDocument();
  });
});
