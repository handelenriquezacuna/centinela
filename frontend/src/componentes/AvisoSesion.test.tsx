import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import { vi } from "vitest";

import { CLAVE_SESION } from "../api/sesion";
import type { SesionActual } from "../api/tipos";
import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { SESION_AGENTE } from "../pruebas/datos";
import { AvisoSesion, rutaDeLoginVencida } from "./AvisoSesion";

const AHORA = new Date("2026-11-26T12:00:00Z");
const SESION: SesionActual = { ...SESION_AGENTE, expira: "2026-11-26T12:05:00Z" };

function Pantalla() {
  const ubicacion = useLocation();
  // Como el Marco: la sesion sale de la cache; sin ella no hay aviso.
  const { data } = useQuery<SesionActual | null>({ queryKey: CLAVE_SESION, queryFn: () => null, staleTime: Infinity });
  return (
    <div>
      <p data-testid="donde">{ubicacion.pathname + ubicacion.search}</p>
      {data ? <AvisoSesion sesion={data} /> : null}
      <form>
        <label htmlFor="nota">Nota</label>
        <textarea id="nota" />
        <button type="button">Otro boton</button>
      </form>
    </div>
  );
}

function montar() {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  cliente.setQueryData(CLAVE_SESION, SESION);
  const enrutador = createMemoryRouter(
    [
      { path: "/detalle/:id", element: <Pantalla /> },
      { path: "/login", element: <Pantalla /> },
    ],
    { initialEntries: ["/detalle/ALR-001?pestana=reglas"] },
  );
  render(
    <QueryClientProvider client={cliente}>
      <RouterProvider router={enrutador} />
    </QueryClientProvider>,
  );
  return { cliente, enrutador };
}

async function avanzar(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(AHORA);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Aviso de sesion por vencer", () => {
  it("no aparece hasta un minuto antes y entonces es un alertdialog con el foco dentro", async () => {
    montar();
    await avanzar(0);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await avanzar(3 * 60_000 + 59_000);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    await avanzar(1_000);
    const aviso = screen.getByRole("alertdialog", { name: "Tu sesión vence en 1 minuto" });
    expect(aviso).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Seguir trabajando" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Salir" })).toBeInTheDocument();
  });

  it("Seguir trabajando renueva, cierra el aviso, devuelve el foco y mueve la expiracion", async () => {
    const api = instalarApiFalsa({
      "POST /api/v1/sesion/renovar": { cuerpo: { token: "x", tipo: "Bearer", agente_id: "AGT-901", rol: "agente", expira: "2026-11-26T13:00:00Z" } },
    });
    const { cliente } = montar();
    screen.getByRole("button", { name: "Otro boton" }).focus();
    await avanzar(4 * 60_000);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Seguir trabajando" }));
    await avanzar(0);

    expect(api.pedidos.filter((p) => p.metodo === "POST").map((p) => p.ruta)).toEqual(["/api/v1/sesion/renovar"]);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Otro boton" })).toHaveFocus();
    expect(cliente.getQueryData<SesionActual>(CLAVE_SESION)?.expira).toBe("2026-11-26T13:00:00Z");
  });

  it("dice que hay texto sin enviar cuando un formulario tiene cambios", async () => {
    montar();
    const nota = screen.getByLabelText("Nota");
    fireEvent.input(nota, { target: { value: "Cliente confirma que no fue el" } });
    await avanzar(4 * 60_000);

    expect(screen.getByRole("alertdialog")).toHaveTextContent("Tienes texto escrito sin enviar");
  });

  it("sin cambios en formularios no habla de texto sin enviar", async () => {
    montar();
    await avanzar(4 * 60_000);
    expect(screen.getByRole("alertdialog")).not.toHaveTextContent("sin enviar");
  });

  it("al vencer lleva al login con el destino actual y el motivo, y suelta la sesion", async () => {
    const { cliente, enrutador } = montar();
    await avanzar(5 * 60_000);

    expect(enrutador.state.location.pathname).toBe("/login");
    const parametros = new URLSearchParams(enrutador.state.location.search);
    expect(parametros.get("siguiente")).toBe("/detalle/ALR-001?pestana=reglas");
    expect(parametros.get("motivo")).toBe("vencida");
    expect(cliente.getQueryData(CLAVE_SESION)).toBeNull();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("si el servidor niega la renovacion (401) tambien lleva al login", async () => {
    instalarApiFalsa({ "POST /api/v1/sesion/renovar": errorApi(401, "no_autenticado", "x") });
    const { enrutador } = montar();
    await avanzar(4 * 60_000);

    fireEvent.click(screen.getByRole("button", { name: "Seguir trabajando" }));
    await avanzar(0);

    expect(enrutador.state.location.pathname).toBe("/login");
    expect(enrutador.state.location.search).toContain("motivo=vencida");
  });

  it("un fallo que no es de sesion deja el aviso abierto con el error", async () => {
    instalarApiFalsa({ "POST /api/v1/sesion/renovar": errorApi(503, "no_disponible", "base caida") });
    const { enrutador } = montar();
    await avanzar(4 * 60_000);

    fireEvent.click(screen.getByRole("button", { name: "Seguir trabajando" }));
    await avanzar(0);

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("No se pudo renovar la sesión");
    expect(enrutador.state.location.pathname).toBe("/detalle/ALR-001");
  });

  it("Salir cierra la sesion y va al login", async () => {
    const api = instalarApiFalsa({ "DELETE /api/v1/sesion": { estado: 204 } });
    const { enrutador } = montar();
    await avanzar(4 * 60_000);

    fireEvent.click(screen.getByRole("button", { name: "Salir" }));
    await avanzar(0);

    expect(api.pedidos.map((p) => `${p.metodo} ${p.ruta}`)).toEqual(["DELETE /api/v1/sesion"]);
    expect(enrutador.state.location.pathname).toBe("/login");
  });

  it("Tab no saca el foco del aviso", async () => {
    montar();
    await avanzar(4 * 60_000);
    const salir = screen.getByRole("button", { name: "Salir" });
    salir.focus();

    fireEvent.keyDown(salir, { key: "Tab" });

    expect(screen.getByRole("button", { name: "Seguir trabajando" })).toHaveFocus();
  });
});

describe("rutaDeLoginVencida", () => {
  it("conserva el destino y agrega el motivo", () => {
    expect(rutaDeLoginVencida("/busqueda?q=1")).toBe("/login?siguiente=%2Fbusqueda%3Fq%3D1&motivo=vencida");
  });
  it("sin destino util, solo el motivo", () => {
    expect(rutaDeLoginVencida("/")).toBe("/login?motivo=vencida");
  });
});
