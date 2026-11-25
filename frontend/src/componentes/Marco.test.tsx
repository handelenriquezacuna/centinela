import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";

import { RUTAS } from "../App";
import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { SESION_AGENTE } from "../pruebas/datos";
import { EventSourceFalso } from "../pruebas/eventSourceFalso";
import type { SesionActual } from "../api/tipos";
import { vi } from "vitest";

const SIN_REZAGO = { atrasada: false, por_conteo: false, pendientes: 0, aviso_desde: 500, atraso_segundos: 0, aviso_atraso_desde: 15, atraso_legible: "", segundos_para_ponerse_al_dia: null, estimado: "", medido_en: null };

function montar(ruta: string) {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const enrutador = createMemoryRouter(RUTAS, { initialEntries: [ruta] });
  render(
    <QueryClientProvider client={cliente}>
      <RouterProvider router={enrutador} />
    </QueryClientProvider>,
  );
  return enrutador;
}

beforeEach(() => {
  EventSourceFalso.instancias = [];
  vi.stubGlobal("EventSource", EventSourceFalso);
});

describe("Navegacion y sesion", () => {
  it("la navegacion sale de la sesion, no de una lista propia", async () => {
    instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_AGENTE },
      "GET /api/v1/casos-destino": { cuerpo: { total: 0, limite: 50, desplazamiento: 0, elementos: [] } },
      "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO },
    });
    montar("/panel");
    const nav = await screen.findByRole("navigation", { name: "Navegación principal" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Panel", "Búsqueda", "Red de mulas"]);
    expect(within(nav).queryByRole("link", { name: "Tablero" })).not.toBeInTheDocument();
  });

  it("una pantalla que el rol no puede abrir muestra acceso denegado", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: SESION_AGENTE } });
    montar("/tablero");
    expect(await screen.findByRole("heading", { name: "Acceso denegado" })).toBeInTheDocument();
  });

  it("sin sesion lleva al login conservando el destino", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": errorApi(401, "no_autenticado", "Hace falta iniciar sesion") });
    const enrutador = montar("/busqueda?cuenta=CTA-001");
    await waitFor(() => expect(enrutador.state.location.pathname).toBe("/login"));
    expect(enrutador.state.location.search).toContain("siguiente=");
  });

  it("una ruta que no existe muestra la pagina 404 propia", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: SESION_AGENTE } });
    montar("/no-existe");
    expect(await screen.findByRole("heading", { name: "Página no encontrada" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Volver al panel" })).toHaveAttribute("href", "/panel");
  });

  it("Salir llama DELETE /api/v1/sesion y vuelve al login", async () => {
    const usuario = userEvent.setup();
    const sesion: SesionActual = SESION_AGENTE;
    const api = instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: sesion },
      "DELETE /api/v1/sesion": { estado: 204 },
      "GET /api/v1/casos-destino": { cuerpo: { total: 0, limite: 50, desplazamiento: 0, elementos: [] } },
      "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO },
    });
    const enrutador = montar("/panel");
    await usuario.click(await screen.findByRole("button", { name: "Salir" }));

    await waitFor(() => expect(enrutador.state.location.pathname).toBe("/login"));
    expect(api.pedidos.some((p) => p.metodo === "DELETE" && p.ruta === "/api/v1/sesion")).toBe(true);
  });
});
