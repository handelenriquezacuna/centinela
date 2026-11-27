import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";

import { RUTAS } from "../App";
import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { SESION_ADMINISTRADOR, SESION_AGENTE, SESION_SUPERVISOR } from "../pruebas/datos";
import type { EstadoFuncional, SaludBase } from "../api/tipos";

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

const ESTADO: EstadoFuncional = {
  estado: "degradado",
  version: "0.1.0",
  entorno: "dev",
  tolerancia_retraso_segundos: 30,
  advertencias: ["La clave de sesión es efímera"],
  tareas: [
    {
      nombre: "d1_deteccion",
      estado: "retrasada",
      eventos_procesados: 1500,
      ultimo_evento: "2026-11-24T02:25:00Z",
      retraso_segundos: 95,
      ultimo_error: null,
      historia: null,
      advertencia: null,
      latencia_ms: { n: 40, p50: 12, p95: 80, max: 120 },
      rezago: { pendientes: 340, antiguedad_segundos: 20, ritmo_por_segundo: 50, segundos_para_ponerse_al_dia: 7, atraso_segundos: 4.5, medido_en: "2026-11-24T02:25:00Z" },
    },
    {
      nombre: "latido",
      estado: "corriendo",
      eventos_procesados: 9,
      ultimo_evento: "2026-11-24T02:25:00Z",
      retraso_segundos: 2,
      ultimo_error: null,
      historia: null,
      advertencia: null,
      latencia_ms: null,
      rezago: null,
    },
  ],
};
const SALUD: SaludBase = { estado: "ok", version: "0.1.0", entorno: "dev", mongo_conectado: true, replica_set: "rsfraude", es_primario: true, detalle: null };

describe("pagina de inicio por rol", () => {
  it.each([
    ["agente", SESION_AGENTE, "/panel"],
    ["supervisor", SESION_SUPERVISOR, "/tablero"],
    ["administrador", SESION_ADMINISTRADOR, "/administracion/salud"],
  ])("la raiz lleva al %s a su inicio", async (_rol, sesion, inicio) => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: sesion } });
    const enrutador = montar("/");
    await waitFor(() => expect(enrutador.state.location.pathname).toBe(inicio));
  });

  it("sin sesion la raiz lleva al login", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": errorApi(401, "no_autenticado", "Hace falta iniciar sesion") });
    const enrutador = montar("/");
    await waitFor(() => expect(enrutador.state.location.pathname).toBe("/login"));
  });

  it("el logotipo lleva al inicio del rol", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: SESION_SUPERVISOR } });
    montar("/reglas");
    await waitFor(() => expect(screen.getByRole("link", { name: /Centinela/ })).toHaveAttribute("href", "/tablero"));
  });
});

describe("acceso denegado", () => {
  it("dice quien puede abrir la pantalla, con que rol se entro y adonde ir", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: SESION_SUPERVISOR } });
    montar("/administracion/respaldos");
    expect(await screen.findByRole("heading", { name: "Acceso denegado" })).toBeInTheDocument();
    expect(screen.getByText(/Esta pantalla es para el administrador\./)).toBeInTheDocument();
    expect(screen.getByText(/Entraste como supervisor\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ir a mi pantalla de inicio" })).toHaveAttribute("href", "/tablero");
  });
});

describe("area de Administracion", () => {
  it("/administracion lleva a Salud del sistema", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: SESION_ADMINISTRADOR }, "GET /estado": { cuerpo: ESTADO }, "GET /salud": { cuerpo: SALUD } });
    const enrutador = montar("/administracion");
    await waitFor(() => expect(enrutador.state.location.pathname).toBe("/administracion/salud"));
  });

  it("Salud muestra la base, la replica, D1 y las tareas, no el JSON crudo", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: SESION_ADMINISTRADOR }, "GET /estado": { cuerpo: ESTADO }, "GET /salud": { cuerpo: SALUD } });
    montar("/administracion/salud");
    expect(await screen.findByRole("heading", { level: 1, name: "Salud del sistema" })).toBeInTheDocument();
    expect(await screen.findByText("Degradado")).toBeInTheDocument();
    expect(screen.getByText("Conectada")).toBeInTheDocument();
    expect(screen.getByText("rsfraude")).toBeInTheDocument();
    expect(screen.getByText("Nodo primario")).toBeInTheDocument();
    expect(screen.getAllByText("340")).toHaveLength(2); // por evaluar: tarjeta de D1 y tabla
    expect(screen.getByText(/Latencia p95 80 ms en 40 mediciones; atraso 4,5 s/)).toBeInTheDocument();
    expect(screen.getByText("La clave de sesión es efímera")).toBeInTheDocument();
    const tabla = screen.getByRole("table", { name: /Tareas de fondo/ });
    expect(within(tabla).getByText("D1 · Detección")).toBeInTheDocument();
    expect(within(tabla).getByText("12 ms / 80 ms")).toBeInTheDocument();
    expect(within(tabla).getByText("Retrasada")).toBeInTheDocument();
  });

  it("si /estado falla lo dice con un alert y deja reintentar", async () => {
    instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_ADMINISTRADOR },
      "GET /estado": errorApi(500, "error_interno", "Error interno del servidor"),
      "GET /salud": { cuerpo: SALUD },
    });
    montar("/administracion/salud");
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("Error interno del servidor");
    expect(screen.getAllByRole("button", { name: "Reintentar" }).length).toBeGreaterThan(0);
  });

  it("Respaldos lista los respaldos (el detalle esta en Respaldos.test.tsx)", async () => {
    instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_ADMINISTRADOR },
      "GET /api/v1/respaldos": { cuerpo: { total: 0, en_curso: false, elementos: [] } },
    });
    montar("/administracion/respaldos");
    expect(await screen.findByRole("heading", { level: 1, name: "Respaldos" })).toBeInTheDocument();
    expect(await screen.findByText("Todavía no hay respaldos.")).toBeInTheDocument();
  });

  it("Usuarios del portal lista las cuentas, marca las bloqueadas y muestra los comandos", async () => {
    const api = instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_ADMINISTRADOR },
      "GET /api/v1/usuarios": {
        cuerpo: {
          total: 2,
          bloqueados: 1,
          elementos: [
            { agente_id: "AGT-001", nombre: "Jose Solis", rol: "agente", activo: true, tiene_credencial: true, intentos_fallidos: 0, bloqueado: false, bloqueado_hasta: null },
            { agente_id: "AGT-002", nombre: "Laura Mendez", rol: "supervisor", activo: true, tiene_credencial: true, intentos_fallidos: 5, bloqueado: true, bloqueado_hasta: "2099-01-01T00:15:00Z" },
          ],
        },
      },
    });
    montar("/administracion/usuarios");
    expect(await screen.findByText("python tareas.py usuarios --help")).toBeInTheDocument();
    expect(screen.getByText("python tareas.py usuarios --agente AGT-001 --desbloquear")).toBeInTheDocument();
    expect(await screen.findByText("Laura Mendez")).toBeInTheDocument();
    expect(screen.getByText("Bloqueado")).toBeInTheDocument();
    expect(screen.getByText("Cuentas bloqueadas: 1.")).toBeInTheDocument();
    expect(api.pedidos.map((p) => p.ruta)).toEqual(["/api/v1/sesion", "/api/v1/usuarios"]);
  });

  it("un agente no abre Salud", async () => {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: SESION_AGENTE } });
    montar("/administracion/salud");
    expect(await screen.findByRole("heading", { name: "Acceso denegado" })).toBeInTheDocument();
  });
});
