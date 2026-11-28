import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";

import type { EnlaceDelCliente } from "../api/tiposCliente";
import { instalarApiFalsa } from "../pruebas/apiFalsa";
import { Cliente } from "./Cliente";

const TOKEN = "T".repeat(43);

function enlace(cambios: Partial<EnlaceDelCliente> = {}): EnlaceDelCliente {
  return {
    fase: "pendiente",
    puede_responder: true,
    transferencia: {
      monto_crc: 280000,
      // 04:47 UTC es 22:47 en Costa Rica.
      fecha: "2026-09-25T04:47:00Z",
      cuenta_terminada_en: "4455",
      destino_enmascarado: "6033-****",
    },
    vence: "2026-09-25T05:02:00Z",
    respuesta: null,
    caso: null,
    ayuda: { telefono: "800-000-0000", es_ejemplo: true },
    sondeo_segundos: 3,
    ...cambios,
  };
}

function montar() {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter initialEntries={[`/c/${TOKEN}`]}>
        <Routes>
          <Route path="/c/:token" element={<Cliente />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Cliente (pagina del enlace del SMS)", () => {
  beforeEach(() => {
    // jsdom no implementa <dialog>.showModal/close.
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute("open");
      this.dispatchEvent(new Event("close"));
    };
  });

  it("muestra la transferencia en palabras simples, en colones y hora de Costa Rica, sin enlaces", async () => {
    instalarApiFalsa({ [`GET /api/v1/cliente/${TOKEN}`]: { cuerpo: enlace() } });
    montar();

    expect(await screen.findByRole("heading", { name: "¿Fuiste tú?" })).toBeInTheDocument();
    expect(screen.getByText("₡280.000")).toBeInTheDocument();
    expect(screen.getByText(/24\/09\/2026 22:47/)).toBeInTheDocument();
    expect(screen.getByText("terminada en 4455")).toBeInTheDocument();
    expect(screen.getByText("6033-****")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sí, fui yo" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "No fui yo" })).toBeInTheDocument();
    expect(screen.getByText(/\(número de ejemplo\)/)).toBeInTheDocument();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });

  it("responder pide confirmacion y manda el valor; despues muestra el estado del caso", async () => {
    const usuario = userEvent.setup();
    const { pedidos } = instalarApiFalsa({
      [`GET /api/v1/cliente/${TOKEN}`]: { cuerpo: enlace() },
      [`POST /api/v1/cliente/${TOKEN}/respuesta`]: {
        cuerpo: enlace({
          fase: "respondida",
          puede_responder: false,
          respuesta: { valor: "no_fui_yo", fecha: "2026-09-25T04:48:00Z" },
          caso: { codigo: "recibida", titulo: "Recibimos tu respuesta", detalle: "Un agente la verá pronto." },
        }),
      },
    });
    montar();

    await usuario.click(await screen.findByRole("button", { name: "No fui yo" }));
    expect(pedidos.filter((p) => p.metodo === "POST")).toHaveLength(0); // aun no: falta confirmar
    const dialogo = screen.getByRole("dialog");
    await usuario.click(within(dialogo).getByRole("button", { name: "Sí, no fui yo" }));

    expect(await screen.findByText("Recibimos tu respuesta")).toBeInTheDocument();
    expect(pedidos.filter((p) => p.metodo === "POST").map((p) => p.cuerpo)).toEqual([{ valor: "no_fui_yo" }]);
    expect(screen.queryByRole("button", { name: "No fui yo" })).toBeNull();
    expect(screen.getByText("Tu respuesta:", { exact: false })).toHaveTextContent("No fui yo");
  });

  it("cancelar la confirmacion no manda nada", async () => {
    const usuario = userEvent.setup();
    const { pedidos } = instalarApiFalsa({ [`GET /api/v1/cliente/${TOKEN}`]: { cuerpo: enlace() } });
    montar();

    await usuario.click(await screen.findByRole("button", { name: "Sí, fui yo" }));
    await usuario.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancelar" }));

    expect(pedidos.filter((p) => p.metodo === "POST")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Sí, fui yo" })).toBeInTheDocument();
  });

  it("un caso confirmado dice que llame al numero de ayuda", async () => {
    instalarApiFalsa({
      [`GET /api/v1/cliente/${TOKEN}`]: {
        cuerpo: enlace({
          fase: "respondida",
          puede_responder: false,
          respuesta: { valor: "no_fui_yo", fecha: "2026-09-25T04:48:00Z" },
          caso: { codigo: "fraude_confirmado", titulo: "Confirmamos el fraude: llama al 800-000-0000", detalle: "Llama ahora." },
        }),
      },
    });
    montar();

    expect(await screen.findByText("Confirmamos el fraude: llama al 800-000-0000")).toBeInTheDocument();
  });

  it("un enlace vencido o inexistente muestra una pagina clara con el numero de ayuda", async () => {
    instalarApiFalsa({
      [`GET /api/v1/cliente/${TOKEN}`]: {
        estado: 404,
        cuerpo: {
          error: {
            codigo: "no_encontrado",
            mensaje: "Este enlace no es válido o ya venció.",
            detalles: [{ campo: "telefono_ayuda", mensaje: "800-000-0000" }],
          },
        },
      },
    });
    montar();

    expect(await screen.findByRole("heading", { name: "Este enlace ya no sirve" })).toBeInTheDocument();
    expect(screen.getByText("800-000-0000")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryAllByRole("link")).toHaveLength(0));
  });
});
