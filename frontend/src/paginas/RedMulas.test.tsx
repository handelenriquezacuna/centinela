import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";

import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { SESION_AGENTE } from "../pruebas/datos";
import { ENTRADA_6033, respuestaGrafo, respuestaSankey, SESION_SUPERVISOR } from "../pruebas/datosRed";
import { RedMulas } from "./RedMulas";

function Ubicacion() {
  const ubicacion = useLocation();
  return <span data-testid="ruta">{ubicacion.pathname}</span>;
}

function montar(ruta = "/red-mulas/6033-9001?desde=2026-09-25T04%3A00%3A00Z") {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter initialEntries={[ruta]}>
        <Routes>
          <Route path="/red-mulas" element={<><RedMulas /><Ubicacion /></>} />
          <Route path="/red-mulas/:cuenta" element={<><RedMulas /><Ubicacion /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const BASE = {
  "GET /api/v1/red-mulas/6033-9001/sankey": { cuerpo: respuestaSankey() },
};

describe("Red de mulas", () => {
  it("dibuja el grafo, la tabla de saltos y el resumen de la red", async () => {
    instalarApiFalsa({ ...BASE, "GET /api/v1/sesion": { cuerpo: SESION_AGENTE }, "GET /api/v1/red-mulas/6033-9001/grafo": respuestaGrafo() });
    montar();

    const grafo = await screen.findByRole("img", { name: /Red de mulas de 6033-9001: 4 cuentas y 3 transferencias/ });
    expect(grafo.querySelectorAll("line.arista")).toHaveLength(3);
    expect(grafo.querySelectorAll("g.nodo-grupo")).toHaveLength(4);
    expect(document.querySelector("#red-resumen")).toHaveTextContent("Partida: 6033-9001 desde 24/09/2026 22:00 (hora CR)");
    const filas = within(document.querySelector("#tabla-aristas tbody") as HTMLElement).getAllByRole("row");
    expect(filas).toHaveLength(3);
    expect(filas[0]).toHaveTextContent("7104-2288");
    expect(filas[0]).toHaveTextContent("₡276.000");
    expect(filas[2]).toHaveTextContent("6001-3344");
    expect(filas[2]).toHaveTextContent("₡90.000");
    // El Sankey de la misma red va en la pantalla.
    expect(await screen.findByRole("img", { name: /4 transferencias/ })).toBeInTheDocument();
  });

  it("al agente le muestra las propuestas sin botones", async () => {
    instalarApiFalsa({
      ...BASE,
      "GET /api/v1/sesion": { cuerpo: SESION_AGENTE },
      "GET /api/v1/red-mulas/6033-9001/grafo": respuestaGrafo({ entradas: [] }),
    });
    montar();

    await screen.findByRole("img", { name: /Red de mulas de/ });
    expect(within(document.querySelector("#propuestas") as HTMLElement).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /Agregar .* a lista de riesgo/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Dar de baja/ })).toBeNull();
    expect(screen.getByText(/decisión de un supervisor o administrador/)).toBeInTheDocument();
  });

  it("el supervisor aprueba una propuesta y la lista se actualiza", async () => {
    let aprobada = false;
    const { pedidos } = instalarApiFalsa({
      ...BASE,
      "GET /api/v1/sesion": { cuerpo: SESION_SUPERVISOR },
      "GET /api/v1/red-mulas/6033-9001/grafo": () => respuestaGrafo({ propuestas: aprobada ? ["8455-1177", "6001-3344"] : ["7104-2288", "8455-1177", "6001-3344"] }),
      "POST /api/v1/red-mulas/6033-9001/propuestas/7104-2288": () => {
        aprobada = true;
        return { estado: 201, cuerpo: { creada: true, entrada: { ...ENTRADA_6033, _id: "RSK-010", cuenta: "7104-2288" } } };
      },
    });
    const usuario = userEvent.setup();
    montar();

    await usuario.click(await screen.findByRole("button", { name: "Agregar 7104-2288 a lista de riesgo" }));

    expect(await screen.findByRole("status")).toHaveTextContent("La cuenta 7104-2288 se agregó a la lista de riesgo como RSK-010.");
    await waitFor(() => expect(within(document.querySelector("#propuestas") as HTMLElement).getAllByRole("listitem")).toHaveLength(2));
    expect(pedidos.some((p) => p.metodo === "POST" && p.ruta === "/api/v1/red-mulas/6033-9001/propuestas/7104-2288")).toBe(true);
  });

  it("dar de baja pide confirmacion, borra la entrada y deja el aviso", async () => {
    let borrada = false;
    const { pedidos } = instalarApiFalsa({
      ...BASE,
      "GET /api/v1/sesion": { cuerpo: SESION_SUPERVISOR },
      "GET /api/v1/red-mulas/6033-9001/grafo": () => respuestaGrafo({ entradas: borrada ? [] : [ENTRADA_6033] }),
      "DELETE /api/v1/listas-riesgo/RSK-001": () => {
        borrada = true;
        return { estado: 204 };
      },
    });
    const usuario = userEvent.setup();
    montar();

    await usuario.click(await screen.findByRole("button", { name: "Dar de baja RSK-001 de 6033-9001" }));
    const dialogo = await screen.findByRole("dialog", { name: "Dar de baja" });
    expect(dialogo).toHaveTextContent("Se borra la entrada RSK-001 de 6033-9001. Queda registro en el log.");
    expect(pedidos.some((p) => p.metodo === "DELETE")).toBe(false);
    await usuario.click(within(dialogo).getByRole("button", { name: "Dar de baja" }));

    await waitFor(() => expect(document.querySelector("#entrada-RSK-001")).toBeNull());
    expect(screen.getByRole("status")).toHaveTextContent("Dada de baja: RSK-001 ya no está en la lista.");
  });

  it("cancelar la baja no borra nada", async () => {
    const { pedidos } = instalarApiFalsa({
      ...BASE,
      "GET /api/v1/sesion": { cuerpo: SESION_SUPERVISOR },
      "GET /api/v1/red-mulas/6033-9001/grafo": respuestaGrafo(),
    });
    const usuario = userEvent.setup();
    montar();

    await usuario.click(await screen.findByRole("button", { name: "Dar de baja RSK-001 de 6033-9001" }));
    await usuario.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancelar" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(pedidos.some((p) => p.metodo === "DELETE")).toBe(false);
    expect(document.querySelector("#entrada-RSK-001")).not.toBeNull();
  });

  it("una cuenta que no existe muestra el error de la API", async () => {
    instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_AGENTE },
      "GET /api/v1/red-mulas/CTA-999/grafo": errorApi(404, "no_encontrado", "No existe la cuenta CTA-999"),
    });
    montar("/red-mulas/CTA-999");

    expect(await screen.findByRole("alert")).toHaveTextContent("No existe la cuenta CTA-999");
  });

  it("el formulario valida la cuenta y navega a su red", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: SESION_AGENTE } });
    const usuario = userEvent.setup();
    montar("/red-mulas");

    await usuario.type(screen.getByLabelText(/Cuenta \(SINPE/), "hola");
    await usuario.click(screen.getByRole("button", { name: "Ver red" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("La cuenta debe ser un número SINPE");
    expect(screen.getByTestId("ruta")).toHaveTextContent("/red-mulas");

    await usuario.clear(screen.getByLabelText(/Cuenta \(SINPE/));
    await usuario.type(screen.getByLabelText(/Cuenta \(SINPE/), "6033-9001");
    await usuario.click(screen.getByRole("button", { name: "Ver red" }));
    await waitFor(() => expect(screen.getByTestId("ruta")).toHaveTextContent("/red-mulas/6033-9001"));
  });

  it("una cuenta sin salidas dice que no hay red y no deja un hueco", async () => {
    const sin = respuestaGrafo();
    instalarApiFalsa({
      ...BASE,
      "GET /api/v1/sesion": { cuerpo: SESION_AGENTE },
      "GET /api/v1/red-mulas/6033-9001/grafo": { cuerpo: { ...sin.cuerpo, grafo: null } },
    });
    montar();
    expect(await screen.findByText("Esta cuenta no tiene salidas registradas en la ventana.")).toBeInTheDocument();
  });

  it("si la red no carga ofrece reintentar", async () => {
    instalarApiFalsa({ ...BASE, "GET /api/v1/sesion": { cuerpo: SESION_AGENTE }, "GET /api/v1/red-mulas/6033-9001/grafo": errorApi(500, "interno", "La base no respondio") });
    montar();
    expect(await screen.findByRole("alert")).toHaveTextContent("La base no respondio");
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });
});
