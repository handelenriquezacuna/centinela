import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { MensajeSms } from "../api/tiposCliente";
import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { SimuladorSms } from "./SimuladorSms";

const TOKEN = "T".repeat(43);

function mensaje(cambios: Partial<MensajeSms> = {}): MensajeSms {
  return {
    notificacion_id: "NOT-000001-SMS",
    alerta_id: "ALR-000001",
    para: "8***-**55",
    fecha: "2026-09-25T04:47:00Z",
    estado_envio: "enviado",
    texto: `Banco: detectamos una transferencia SINPE de ₡280.000 desde tu cuenta terminada en 4455. ¿Fuiste tú? Responde aquí: http://127.0.0.1:8000/c/${TOKEN}`,
    ruta_enlace: `/c/${TOKEN}`,
    enlace: "vigente",
    respuesta: null,
    ...cambios,
  };
}

function montar() {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter>
        <SimuladorSms />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SimuladorSms", () => {
  it("muestra el SMS con el telefono enmascarado y el enlace clicable (a la ruta relativa)", async () => {
    instalarApiFalsa({ "GET /api/v1/simulador-sms": { cuerpo: { mensajes: [mensaje()] } } });
    montar();

    const sms = await screen.findByRole("article");
    expect(within(sms).getByText("Para 8***-**55")).toBeInTheDocument();
    expect(sms).toHaveTextContent("detectamos una transferencia SINPE de ₡280.000");
    expect(within(sms).getByRole("link")).toHaveAttribute("href", `/c/${TOKEN}`);
    expect(within(sms).getByText("enlace vigente")).toBeInTheDocument();
  });

  it("muestra lo que respondio el cliente y que el enlace ya se uso", async () => {
    instalarApiFalsa({
      "GET /api/v1/simulador-sms": {
        cuerpo: {
          mensajes: [mensaje({ enlace: "usado", respuesta: { valor: "no_fui_yo", fecha: "2026-09-25T04:48:00Z" } })],
        },
      },
    });
    montar();

    expect(await screen.findByText("enlace usado")).toBeInTheDocument();
    expect(screen.getByText("respondió: no fui yo")).toBeInTheDocument();
  });

  it("sin mensajes lo dice", async () => {
    instalarApiFalsa({ "GET /api/v1/simulador-sms": { cuerpo: { mensajes: [] } } });
    montar();

    expect(await screen.findByText("Sin mensajes todavía.")).toBeInTheDocument();
  });

  it("con la bandera apagada (404) explica que esta desactivado", async () => {
    instalarApiFalsa({ "GET /api/v1/simulador-sms": errorApi(404, "no_encontrado", "El simulador de SMS está desactivado en este entorno.") });
    montar();

    expect(await screen.findByText("El simulador está desactivado en este entorno")).toBeInTheDocument();
    expect(screen.queryByRole("article")).toBeNull();
  });
});
