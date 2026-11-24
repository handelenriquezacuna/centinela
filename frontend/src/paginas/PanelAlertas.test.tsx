import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { vi } from "vitest";

import type { Alerta } from "../api/tipos";
import { instalarApiFalsa } from "../pruebas/apiFalsa";
import { alertaDeCaso, casoDestino } from "../pruebas/datos";
import { EventSourceFalso } from "../pruebas/eventSourceFalso";
import { Panel } from "./Panel";

const SIN_REZAGO = { atrasada: false, pendientes: 0, aviso_desde: 500, segundos_para_ponerse_al_dia: null, estimado: "", medido_en: null };

function alerta(id: string, campos: Partial<Alerta> = {}): Alerta {
  return {
    _id: id,
    esquema_version: 2,
    transaccion_id: `TXN-${id}`,
    puntaje: 60,
    severidad: "ALTA",
    estado: "nueva",
    agente_id: null,
    fecha_creacion: "2026-10-15T04:47:00Z",
    cuenta_origen: "8712-4455",
    cuenta_destino: "6033-9001",
    monto_crc: 280_000,
    moneda: "CRC",
    cliente_nombre: "María Rodríguez Vargas",
    reglas_disparadas: [],
    historial: [],
    ...campos,
  };
}

const COLA = [alerta("ALR-001", { puntaje: 75, severidad: "CRITICA", monto_crc: 750_000 }), alerta("ALR-002", { puntaje: 40, severidad: "MEDIA" })];

function pagina(...elementos: unknown[]) {
  return { cuerpo: { total: elementos.length, limite: 50, desplazamiento: 0, elementos } };
}

function montar(ruta = "/panel?vista=alertas") {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter initialEntries={[ruta]}>
        <Routes>
          <Route path="/panel" element={<Panel />} />
          <Route path="/detalle/:alertaId" element={<p>Pantalla de detalle</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function ids(): string[] {
  return [...document.querySelectorAll("#cola [role=row]")].map((fila) => fila.id.replace("alerta-", ""));
}

beforeEach(() => {
  EventSourceFalso.instancias = [];
  vi.stubGlobal("EventSource", EventSourceFalso);
  window.localStorage.clear();
});

describe("Panel por alerta", () => {
  it("pinta la cola con colones enteros y hora de Costa Rica, en el orden del servidor", async () => {
    instalarApiFalsa({ "GET /api/v1/alertas": pagina(...COLA), "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO } });
    montar();

    const tabla = await screen.findByRole("table", { name: "Cola de alertas" });
    expect(ids()).toEqual(["ALR-001", "ALR-002"]);
    const primera = within(tabla).getByRole("link", { name: "ALR-001" }).closest("[role=row]") as HTMLElement;
    expect(primera).toHaveAttribute("data-monto-crc", "750000");
    expect(within(primera).getByText("₡750.000")).toBeInTheDocument();
    expect(within(primera).getByText("14/10/2026 22:47")).toBeInTheDocument();
    expect(within(primera).getByText("Crítica")).toBeInTheDocument();
    expect(within(primera).getByRole("link", { name: "ALR-001" })).toHaveAttribute("href", "/detalle/ALR-001");
    expect(screen.getByRole("link", { name: "Por alerta" })).toHaveAttribute("aria-current", "page");
  });

  it("una alerta que llega por el canal entra en su lugar sin pedir la cola de nuevo", async () => {
    const api = instalarApiFalsa({ "GET /api/v1/alertas": pagina(...COLA), "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO } });
    montar();
    await screen.findByRole("table", { name: "Cola de alertas" });
    const canal = EventSourceFalso.ultima();
    act(() => canal.abrir());
    expect(document.getElementById("estado-canal")).toHaveTextContent("en vivo");

    act(() => canal.emitir("alerta", { formato: 1, operacion: "insert", alerta: alerta("ALR-900", { puntaje: 55, monto_crc: 600_000 }) }, "tok"));

    await waitFor(() => expect(ids()).toEqual(["ALR-001", "ALR-900", "ALR-002"]));
    expect(document.getElementById("alerta-ALR-900")).toHaveTextContent("₡600.000");
    expect(document.getElementById("aviso-nuevas")).toHaveTextContent("ALR-900");
    expect(api.pedidos.filter((p) => p.ruta === "/api/v1/alertas")).toHaveLength(1);
  });

  it("una alerta que cambia de estado se reemplaza y se reordena; no se duplica", async () => {
    instalarApiFalsa({ "GET /api/v1/alertas": pagina(...COLA), "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO } });
    montar();
    await screen.findByRole("table", { name: "Cola de alertas" });

    act(() =>
      EventSourceFalso.ultima().emitir("alerta", {
        formato: 1,
        operacion: "update",
        alerta: alerta("ALR-002", { puntaje: 90, estado: "en_revision" }),
      }),
    );

    await waitFor(() => expect(ids()).toEqual(["ALR-002", "ALR-001"]));
    expect(document.getElementById("alerta-ALR-002")).toHaveAttribute("data-estado", "en_revision");
    expect(within(document.getElementById("alerta-ALR-002") as HTMLElement).getByText("En revisión")).toBeInTheDocument();
  });

  it("resincronizar y la reconexion piden la cola entera; un evento de otro formato se ignora", async () => {
    const api = instalarApiFalsa({ "GET /api/v1/alertas": pagina(...COLA), "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO } });
    montar();
    await screen.findByRole("table", { name: "Cola de alertas" });
    const canal = EventSourceFalso.ultima();
    const pedidosDeCola = () => api.pedidos.filter((p) => p.ruta === "/api/v1/alertas").length;

    act(() => canal.emitir("alerta", { formato: 2, alerta: alerta("ALR-777") }));
    expect(ids()).toEqual(["ALR-001", "ALR-002"]);

    act(() => canal.emitir("resincronizar", { formato: 1, motivo: "el token de reanudacion ya no sirve" }));
    await waitFor(() => expect(pedidosDeCola()).toBe(2));

    act(() => canal.fallar());
    expect(document.getElementById("estado-canal")).toHaveTextContent("reconectando");
    act(() => canal.abrir());
    await waitFor(() => expect(pedidosDeCola()).toBe(3));
  });

  it("muestra el aviso de rezago y la cola vacia", async () => {
    instalarApiFalsa({
      "GET /api/v1/alertas": pagina(),
      "GET /api/v1/deteccion/rezago": { cuerpo: { ...SIN_REZAGO, atrasada: true, pendientes: 9988, estimado: "2 min" } },
    });
    montar();
    expect(await screen.findByText("No hay alertas en la cola.")).toBeInTheDocument();
    expect((await screen.findByText(/Detección atrasada/)).closest("[role=status]")).toHaveTextContent("unos 2 min");
  });
});

describe("Conmutador de vista", () => {
  it("por omision es por cuenta destino; la eleccion se recuerda", async () => {
    const usuario = userEvent.setup();
    instalarApiFalsa({
      "GET /api/v1/casos-destino": pagina(casoDestino("6033-9001", [alertaDeCaso("ALR-001")])),
      "GET /api/v1/alertas": pagina(...COLA),
      "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO },
    });
    const primero = montar("/panel");
    expect(await screen.findByRole("region", { name: /Cuenta 6033-9001/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Por cuenta destino" })).toHaveAttribute("aria-current", "page");

    await usuario.click(screen.getByRole("link", { name: "Por alerta" }));
    await screen.findByRole("table", { name: "Cola de alertas" });
    expect(window.localStorage.getItem("centinela_vista")).toBe("alertas");
    primero.unmount();

    // Una visita nueva a /panel, sin ?vista=, vuelve a la vista elegida.
    montar("/panel");
    expect(await screen.findByRole("table", { name: "Cola de alertas" })).toBeInTheDocument();
  });
});

describe("Atajos de teclado", () => {
  async function conCola() {
    const usuario = userEvent.setup();
    instalarApiFalsa({ "GET /api/v1/alertas": pagina(...COLA), "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO } });
    montar();
    await screen.findByRole("table", { name: "Cola de alertas" });
    return usuario;
  }

  it("j y k mueven el foco entre filas sin salirse de los extremos", async () => {
    const usuario = await conCola();
    await usuario.keyboard("j");
    expect(document.activeElement).toHaveAttribute("id", "alerta-ALR-001");
    await usuario.keyboard("j");
    expect(document.activeElement).toHaveAttribute("id", "alerta-ALR-002");
    await usuario.keyboard("j");
    expect(document.activeElement).toHaveAttribute("id", "alerta-ALR-002");
    await usuario.keyboard("k");
    expect(document.activeElement).toHaveAttribute("id", "alerta-ALR-001");
  });

  it("k sin foco previo va a la ultima fila", async () => {
    const usuario = await conCola();
    await usuario.keyboard("k");
    expect(document.activeElement).toHaveAttribute("id", "alerta-ALR-002");
  });

  it("Enter abre el detalle de la fila con el foco", async () => {
    const usuario = await conCola();
    await usuario.keyboard("j");
    await usuario.keyboard("{Enter}");
    expect(await screen.findByText("Pantalla de detalle")).toBeInTheDocument();
  });

  it("? abre y cierra la ayuda", async () => {
    const usuario = await conCola();
    const ayuda = document.getElementById("ayuda-atajos") as HTMLDialogElement;
    expect(ayuda).not.toHaveAttribute("open");
    await usuario.keyboard("?");
    expect(ayuda).toHaveAttribute("open");
    expect(within(ayuda).getByRole("heading", { name: "Atajos de teclado" })).toBeInTheDocument();
    await usuario.keyboard("?");
    expect(ayuda).not.toHaveAttribute("open");
  });

  it("se ignoran en campos de texto y con Ctrl, Alt o Meta", async () => {
    const usuario = await conCola();
    const campo = document.createElement("input");
    campo.type = "text";
    document.body.append(campo);
    campo.focus();
    await usuario.keyboard("jk?");
    expect(campo).toHaveValue("jk?");
    expect(document.activeElement).toBe(campo);
    expect(document.getElementById("ayuda-atajos")).not.toHaveAttribute("open");
    campo.remove();

    await usuario.keyboard("{Control>}j{/Control}");
    await usuario.keyboard("{Alt>}j{/Alt}");
    await usuario.keyboard("{Meta>}j{/Meta}");
    expect(document.activeElement).toBe(document.body);
  });

  it("x marca la alerta de la fila en la vista por cuenta destino", async () => {
    const usuario = userEvent.setup();
    instalarApiFalsa({
      "GET /api/v1/casos-destino": pagina(casoDestino("6033-9001", [alertaDeCaso("ALR-001"), alertaDeCaso("ALR-002")])),
      "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO },
    });
    montar("/panel?vista=casos");
    const cuenta = await screen.findByRole("region", { name: /Cuenta 6033-9001/ });

    await usuario.keyboard("j");
    expect(document.activeElement).toHaveAttribute("id", "alerta-ALR-001");
    await usuario.keyboard("x");
    expect(within(cuenta).getByRole("checkbox", { name: "Seleccionar ALR-001" })).toBeChecked();
    expect(within(cuenta).getByText("1 seleccionadas")).toBeInTheDocument();
    await usuario.keyboard("x");
    expect(within(cuenta).getByRole("checkbox", { name: "Seleccionar ALR-001" })).not.toBeChecked();
  });
});
