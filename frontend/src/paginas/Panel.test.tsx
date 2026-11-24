import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import { RUTA_EVENTOS } from "../tiempo-real/eventosAlertas";
import { instalarApiFalsa } from "../pruebas/apiFalsa";
import { alertaDeCaso, casoDestino } from "../pruebas/datos";
import { EventSourceFalso } from "../pruebas/eventSourceFalso";
import { Panel } from "./Panel";

const MULA = casoDestino("6033-9001", [alertaDeCaso("ALR-001"), alertaDeCaso("ALR-002", { monto_crc: 280_000, puntaje: 60, severidad: "ALTA" })], {
  sugerencia: "2 víctimas y la cuenta ya está reportada: candidata a confirmar.",
  en_lista_riesgo: true,
});
const NUEVA = casoDestino("7001-0010", [alertaDeCaso("ALR-900", { cuenta_destino: "7001-0010", monto_crc: 600_000 })]);
const SIN_REZAGO = { atrasada: false, pendientes: 0, aviso_desde: 500, segundos_para_ponerse_al_dia: null, estimado: "", medido_en: null };

function pagina(...elementos: unknown[]) {
  return { cuerpo: { total: elementos.length, limite: 50, desplazamiento: 0, elementos } };
}

function montar() {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter>
        <Panel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  EventSourceFalso.instancias = [];
  vi.stubGlobal("EventSource", EventSourceFalso);
});

describe("Panel por cuenta destino", () => {
  it("pinta cada cuenta con colones enteros, hora de Costa Rica y textos con tilde", async () => {
    instalarApiFalsa({ "GET /api/v1/casos-destino": pagina(MULA), "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO } });
    montar();

    const cuenta = await screen.findByRole("region", { name: /Cuenta 6033-9001/ });
    expect(within(cuenta).getByText("₡1.030.000")).toBeInTheDocument();
    expect(within(cuenta).getAllByText("14/10/2026 22:47").length).toBeGreaterThan(0);
    expect(within(cuenta).getByText("Víctimas")).toBeInTheDocument();
    expect(within(cuenta).getByText("En lista de riesgo")).toBeInTheDocument();
    expect(within(cuenta).getByText(/candidata a confirmar/)).toBeInTheDocument();
    const tabla = within(cuenta).getByRole("table", { name: /Alertas abiertas hacia la cuenta destino 6033-9001/ });
    expect(within(tabla).getAllByRole("columnheader").map((c) => c.textContent)).toContain("Creada (hora CR)");
    const fila = within(tabla).getByRole("checkbox", { name: "Seleccionar ALR-002" }).closest("tr");
    expect(fila).toHaveAttribute("data-monto-crc", "280000");
    expect(within(fila as HTMLElement).getByText("₡280.000")).toBeInTheDocument();
    expect(within(cuenta).getByRole("link", { name: "ALR-001" })).toHaveAttribute("href", "/detalle/ALR-001");
    expect(screen.getByText("1 de 1 cuentas destino con alertas abiertas.")).toBeInTheDocument();
  });

  it("abre el canal JSON con la cookie y muestra su estado", async () => {
    instalarApiFalsa({ "GET /api/v1/casos-destino": pagina(MULA), "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO } });
    montar();
    await screen.findByRole("region", { name: /Cuenta 6033-9001/ });

    const canal = EventSourceFalso.ultima();
    expect(canal.url).toBe(RUTA_EVENTOS);
    expect(canal.withCredentials).toBe(true);
    expect(screen.getByText("conectando")).toBeInTheDocument();
    act(() => canal.abrir());
    expect(document.getElementById("estado-canal")).toHaveTextContent("en vivo");
    act(() => canal.fallar());
    expect(document.getElementById("estado-canal")).toHaveTextContent("reconectando");
  });

  it("una alerta que llega por el canal trae solo el caso de su cuenta, sin recargar", async () => {
    const api = instalarApiFalsa({
      "GET /api/v1/casos-destino": pagina(MULA),
      "GET /api/v1/casos-destino/7001-0010": { cuerpo: NUEVA },
      "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO },
    });
    montar();
    await screen.findByRole("region", { name: /Cuenta 6033-9001/ });
    const canal = EventSourceFalso.ultima();
    act(() => canal.abrir());

    act(() =>
      canal.emitir("alerta", { formato: 1, operacion: "insert", alerta: { _id: "ALR-900", cuenta_destino: "7001-0010" } }, "tok"),
    );

    const nueva = await screen.findByRole("region", { name: /Cuenta 7001-0010/ });
    // Monto total de la cuenta y monto de la fila: los dos en colones enteros.
    expect(within(nueva).getAllByText("₡600.000")).toHaveLength(2);
    expect(document.getElementById("aviso-nuevas")).toHaveTextContent("ALR-900");
    expect(screen.getByText("2 de 2 cuentas destino con alertas abiertas.")).toBeInTheDocument();
    // La lista entera se pidio una sola vez: la alerta trajo solo su caso.
    expect(api.pedidos.filter((p) => p.ruta === "/api/v1/casos-destino")).toHaveLength(1);
  });

  it("una cuenta que ya no tiene abiertas sale de la lista; resincronizar pide la lista entera", async () => {
    const api = instalarApiFalsa({
      "GET /api/v1/casos-destino": pagina(MULA),
      "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO },
    });
    montar();
    await screen.findByRole("region", { name: /Cuenta 6033-9001/ });
    const canal = EventSourceFalso.ultima();

    act(() =>
      canal.emitir("alerta", { formato: 1, operacion: "update", alerta: { _id: "ALR-001", cuenta_destino: "6033-9001" } }),
    );
    await waitFor(() => expect(screen.queryByRole("region", { name: /Cuenta 6033-9001/ })).not.toBeInTheDocument());
    expect(screen.getByText("No hay cuentas destino con alertas abiertas.")).toBeInTheDocument();

    act(() => canal.emitir("resincronizar", { formato: 1, motivo: "el token de reanudacion ya no sirve" }));
    await waitFor(() => expect(api.pedidos.filter((p) => p.ruta === "/api/v1/casos-destino")).toHaveLength(2));
    expect(await screen.findByRole("region", { name: /Cuenta 6033-9001/ })).toBeInTheDocument();
  });

  it("ignora un evento de otro formato", async () => {
    const api = instalarApiFalsa({ "GET /api/v1/casos-destino": pagina(MULA), "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO } });
    montar();
    await screen.findByRole("region", { name: /Cuenta 6033-9001/ });
    act(() => EventSourceFalso.ultima().emitir("alerta", { formato: 2, alerta: { _id: "X", cuenta_destino: "1" } }));
    await new Promise((r) => setTimeout(r, 300));
    expect(api.pedidos.some((p) => p.ruta.startsWith("/api/v1/casos-destino/"))).toBe(false);
  });

  it("aplica una accion en lote con confirmacion y muestra el resultado", async () => {
    const usuario = userEvent.setup();
    const api = instalarApiFalsa({
      "GET /api/v1/casos-destino": pagina(MULA),
      "GET /api/v1/casos-destino/6033-9001": { cuerpo: MULA },
      "GET /api/v1/deteccion/rezago": { cuerpo: SIN_REZAGO },
      "POST /api/v1/alertas/transiciones": {
        cuerpo: {
          solicitadas: 2,
          aplicadas: 1,
          rechazadas: 1,
          resultados: [
            { alerta_id: "ALR-001", aplicada: true, estado_http: 200, codigo: null, mensaje: "", estado_actual: "en_revision", caso_id: null },
            { alerta_id: "ALR-002", aplicada: false, estado_http: 409, codigo: "conflicto", mensaje: "", estado_actual: "en_revision", caso_id: null },
          ],
        },
      },
    });
    montar();
    const cuenta = await screen.findByRole("region", { name: /Cuenta 6033-9001/ });

    await usuario.click(within(cuenta).getByRole("button", { name: "Tomar" }));
    expect(within(cuenta).getByRole("alert")).toHaveTextContent("Selecciona al menos una alerta de la cuenta.");

    await usuario.click(within(cuenta).getByRole("checkbox", { name: /Seleccionar todas/ }));
    expect(within(cuenta).getByText("2 seleccionadas")).toBeInTheDocument();
    await usuario.click(within(cuenta).getByRole("button", { name: "Tomar" }));
    const dialogo = within(cuenta).getByRole("dialog", { hidden: true });
    expect(dialogo).toHaveTextContent("Vas a aplicar «Tomar» a 2 alertas hacia la cuenta destino 6033-9001");
    await usuario.type(within(dialogo).getByLabelText(/Motivo/), "misma mula");
    await usuario.click(within(dialogo).getByRole("button", { name: "Confirmar", hidden: true }));

    const resultado = await within(cuenta).findByText(/Pasaron 1 de 2/);
    expect(resultado.closest("[data-resultado-lote]")).toHaveTextContent("ALR-002 (409): la tomó o resolvió otro agente");
    const lote = api.pedidos.find((p) => p.metodo === "POST");
    expect(lote?.cuerpo).toEqual({ alertas: ["ALR-001", "ALR-002"], destino: "en_revision", nota: "[lote en 6033-9001] misma mula" });
  });

  it("muestra el aviso de rezago cuando D1 esta atrasado", async () => {
    instalarApiFalsa({
      "GET /api/v1/casos-destino": pagina(),
      "GET /api/v1/deteccion/rezago": { cuerpo: { ...SIN_REZAGO, atrasada: true, pendientes: 9988, estimado: "2 min" } },
    });
    montar();
    const aviso = await screen.findByText(/Detección atrasada/);
    expect(aviso.closest("[role=status]")).toHaveTextContent("unos 2 min para ponerse al día");
  });
});
