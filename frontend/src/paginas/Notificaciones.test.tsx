import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { ElementoNotificacion } from "../api/tipos";
import { instalarApiFalsa } from "../pruebas/apiFalsa";
import { Notificaciones } from "./Notificaciones";

function notificacion(numero: number, cambios: Partial<ElementoNotificacion> = {}): ElementoNotificacion {
  return {
    notificacion_id: `NOT-00000${numero}`,
    alerta_id: `ALR-00000${numero}`,
    canal: "correo",
    estado_envio: "enviado",
    // 04:47 UTC es 22:47 en Costa Rica.
    fecha: "2026-09-25T04:47:00Z",
    intentos: 1,
    asunto: `[Centinela] Alerta crítica ALR-00000${numero}`,
    destinatarios: ["guardia@centinela.local"],
    detalle_error: null,
    severidad: "CRITICA",
    puntaje: 75,
    monto_crc: 280000,
    cliente_nombre: "Maria Rodriguez Vargas",
    ...cambios,
  };
}

function montar(ruta = "/notificaciones") {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter initialEntries={[ruta]}>
        <Notificaciones />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Notificaciones", () => {
  it("lista los correos con alerta, cliente, monto en colones enteros, estado e intentos", async () => {
    instalarApiFalsa({
      "GET /api/v1/notificaciones": {
        cuerpo: {
          total: 2,
          limite: 50,
          desplazamiento: 0,
          elementos: [
            notificacion(1),
            notificacion(2, { estado_envio: "fallido", intentos: 3, detalle_error: "ConnectionRefusedError: rechazada" }),
          ],
        },
      },
    });
    montar();

    expect(await screen.findByRole("heading", { name: "Notificaciones" })).toBeInTheDocument();
    const tabla = await screen.findByRole("table", { name: "Notificaciones de alertas críticas" });
    const primera = within(tabla).getByRole("row", { name: /ALR-000001/ });
    expect(primera).toHaveTextContent("24/09/2026 22:47");
    expect(primera).toHaveTextContent("Maria Rodriguez Vargas");
    expect(primera).toHaveTextContent("₡280.000");
    expect(primera).toHaveTextContent("Enviado");
    expect(within(primera).getByRole("link", { name: "ALR-000001" })).toHaveAttribute("href", "/detalle/ALR-000001");
    const fallida = within(tabla).getByRole("row", { name: /ALR-000002/ });
    expect(fallida).toHaveTextContent("Fallido (ConnectionRefusedError: rechazada)");
    expect(document.getElementById("notificaciones-total")).toHaveTextContent("2 en total");
  });

  it("el filtro por estado de la URL viaja a la API", async () => {
    const api = instalarApiFalsa({
      "GET /api/v1/notificaciones": { cuerpo: { total: 0, limite: 50, desplazamiento: 0, elementos: [] } },
    });
    montar("/notificaciones?estado=fallido");

    expect(await screen.findByText("Todavía no se envió ninguna notificación")).toBeInTheDocument();
    expect(api.pedidos.some((p) => p.ruta === "/api/v1/notificaciones?limite=50&desplazamiento=0&estado_envio=fallido")).toBe(true);
  });

  it("una alerta que ya no existe sale sin cliente ni monto", async () => {
    instalarApiFalsa({
      "GET /api/v1/notificaciones": {
        cuerpo: {
          total: 1,
          limite: 50,
          desplazamiento: 0,
          elementos: [notificacion(1, { cliente_nombre: null, monto_crc: null, severidad: null })],
        },
      },
    });
    montar();

    const fila = await screen.findByRole("row", { name: /ALR-000001/ });
    expect(fila).not.toHaveTextContent("₡");
  });

  it("un 403 se dice con el mensaje del servidor", async () => {
    instalarApiFalsa({
      "GET /api/v1/notificaciones": {
        estado: 403,
        cuerpo: { error: { codigo: "prohibido", mensaje: "Tu rol no permite esta operación", detalles: [] } },
      },
    });
    montar();

    expect(
      await screen.findByText("Tu rol no permite esta operación"),
    ).toBeInTheDocument();
  });
});
