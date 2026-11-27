import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

import { RUTAS } from "../App";
import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { SESION_AGENTE } from "../pruebas/datos";
import { EventSourceFalso } from "../pruebas/eventSourceFalso";

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
  vi.stubGlobal("EventSource", EventSourceFalso);
});

describe("Login y sesion", () => {
  it("con motivo=vencida avisa que la sesion vencio y conserva el destino", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": errorApi(401, "no_autenticado", "x") });
    const enrutador = montar("/login?siguiente=%2Fbusqueda&motivo=vencida");

    expect(await screen.findByRole("alert")).toHaveTextContent("Tu sesión venció");
    expect(new URLSearchParams(enrutador.state.location.search).get("siguiente")).toBe("/busqueda");
  });

  it("sin sesion, una pantalla protegida lleva al login conservando el destino", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": errorApi(401, "no_autenticado", "Hace falta iniciar sesion") });
    const enrutador = montar("/detalle/ALR-001");

    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
    expect(enrutador.state.location.pathname).toBe("/login");
    expect(new URLSearchParams(enrutador.state.location.search).get("siguiente")).toBe("/detalle/ALR-001");
  });

  it("entra con la cookie, sin guardar el token, y vuelve a donde iba", async () => {
    const usuario = userEvent.setup();
    let conSesion = false;
    const api = instalarApiFalsa({
      "GET /api/v1/sesion": () => (conSesion ? { cuerpo: SESION_AGENTE } : errorApi(401, "no_autenticado", "x")),
      "POST /api/v1/sesion": () => {
        conSesion = true;
        return { cuerpo: { token: "secreto", tipo: "Bearer", agente_id: "AGT-901", rol: "agente", expira: "2026-11-23T20:00:00Z" } };
      },
    });
    const guardar = vi.spyOn(Storage.prototype, "setItem");
    const enrutador = montar("/login?siguiente=%2Fbusqueda");

    await usuario.type(await screen.findByLabelText("Identificador"), "AGT-901");
    await usuario.type(screen.getByLabelText("Contraseña"), "clave-e2e-0123456789");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByRole("heading", { name: "Búsqueda forense" })).toBeInTheDocument();
    expect(enrutador.state.location.pathname).toBe("/busqueda");
    expect(api.pedidos.find((p) => p.metodo === "POST")?.cuerpo).toEqual({
      agente_id: "AGT-901",
      contrasena: "clave-e2e-0123456789",
    });
    expect(guardar).not.toHaveBeenCalled();
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain("secreto");
    expect(screen.getByText("Agente E2E")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Navegación principal" })).toHaveTextContent("Búsqueda");
  });

  it("un destino externo en siguiente se ignora: vuelve al panel", async () => {
    const usuario = userEvent.setup();
    let conSesion = false;
    instalarApiFalsa({
      "GET /api/v1/sesion": () => (conSesion ? { cuerpo: SESION_AGENTE } : errorApi(401, "no_autenticado", "x")),
      "POST /api/v1/sesion": () => {
        conSesion = true;
        return { cuerpo: {} };
      },
      "GET /api/v1/casos-destino": { cuerpo: { total: 0, limite: 50, desplazamiento: 0, elementos: [] } },
      "GET /api/v1/deteccion/rezago": { cuerpo: { atrasada: false, por_conteo: false, pendientes: null, aviso_desde: 500, atraso_segundos: null, aviso_atraso_desde: 15, atraso_legible: "", segundos_para_ponerse_al_dia: null, estimado: "", medido_en: null } },
    });
    const enrutador = montar("/login?siguiente=%2F%2Fotro.sitio%2Fx");

    await usuario.type(await screen.findByLabelText("Identificador"), "AGT-901");
    await usuario.type(screen.getByLabelText("Contraseña"), "x");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByRole("heading", { name: "Panel de alertas" })).toBeInTheDocument();
    expect(enrutador.state.location.pathname).toBe("/panel");
  });

  it("credenciales malas muestran el mensaje del servidor como alerta", async () => {
    const usuario = userEvent.setup();
    instalarApiFalsa({
      "GET /api/v1/sesion": errorApi(401, "no_autenticado", "x"),
      "POST /api/v1/sesion": errorApi(401, "no_autenticado", "Identificador o contraseña incorrectos, o demasiados intentos. Intenta de nuevo en unos minutos."),
    });
    montar("/login");

    await usuario.type(await screen.findByLabelText("Identificador"), "AGT-901");
    await usuario.type(screen.getByLabelText("Contraseña"), "mala");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Identificador o contraseña incorrectos, o demasiados intentos. Intenta de nuevo en unos minutos.");
    expect(screen.getByLabelText("Contraseña")).toHaveValue("");
  });

  it("un rol sin permiso ve acceso denegado, no la pantalla", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: SESION_AGENTE } });
    montar("/tablero");
    expect(await screen.findByRole("heading", { name: "Acceso denegado" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Tablero" })).not.toBeInTheDocument();
  });

  it("salir borra la sesion en el servidor y vuelve al login", async () => {
    const usuario = userEvent.setup();
    let conSesion = true;
    const api = instalarApiFalsa({
      "GET /api/v1/sesion": () => (conSesion ? { cuerpo: SESION_AGENTE } : errorApi(401, "no_autenticado", "x")),
      "DELETE /api/v1/sesion": () => {
        conSesion = false;
        return { estado: 204 };
      },
    });
    const enrutador = montar("/busqueda");

    await usuario.click(await screen.findByRole("button", { name: "Salir" }));

    expect(await screen.findByRole("heading", { name: "Iniciar sesión" })).toBeInTheDocument();
    expect(enrutador.state.location.pathname).toBe("/login");
    expect(api.pedidos.some((p) => p.metodo === "DELETE" && p.ruta === "/api/v1/sesion")).toBe(true);
  });
});
