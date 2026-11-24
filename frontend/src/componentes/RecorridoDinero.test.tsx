import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { instalarApiFalsa, errorApi } from "../pruebas/apiFalsa";
import { SESION_AGENTE } from "../pruebas/datos";
import { respuestaSankey, SESION_SUPERVISOR } from "../pruebas/datosRed";
import { RecorridoDinero } from "./RecorridoDinero";

function montar() {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <RecorridoDinero cuenta="6033-9001" transaccion="TXN-001" parametros={{ desde: "2026-09-25T04:00:00Z" }} />
    </QueryClientProvider>,
  );
}

const MULA = "8455-1177";

function nodo(cuenta: string) {
  return document.querySelector(`#sankey-svg g.sankey-nodo[data-cuenta="${cuenta}"]`);
}

describe("RecorridoDinero", () => {
  it("pide el Sankey con la transaccion y la hora de partida", async () => {
    const { pedidos } = instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_AGENTE },
      "GET /api/v1/red-mulas/6033-9001/sankey": { cuerpo: respuestaSankey() },
    });
    montar();

    await screen.findByRole("img", { name: /4 transferencias/ });
    const pedido = pedidos.find((p) => p.ruta.includes("/sankey"));
    expect(pedido?.ruta).toContain("transaccion=TXN-001");
    expect(pedido?.ruta).toContain("desde=2026-09-25T04%3A00%3A00Z");
  });

  it("al agente no le ofrece marcar: ni boton ni campo de motivo", async () => {
    instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_AGENTE },
      "GET /api/v1/red-mulas/6033-9001/sankey": { cuerpo: respuestaSankey() },
    });
    montar();

    await screen.findByRole("img", { name: /4 transferencias/ });
    expect(within(document.querySelector("#sankey-cuentas") as HTMLElement).getByText(MULA)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Marcar como mula" })).toBeNull();
    expect(screen.queryByLabelText(/Motivo para marcar/)).toBeNull();
    expect(document.querySelector("#sankey-cuentas li[data-victima='si']")).toHaveTextContent("Víctima: María Rodríguez Vargas");
  });

  it("al supervisor le exige motivo de 5 a 200 y confirmacion; despues el nodo cambia sin recargar", async () => {
    let marcada = false;
    const { pedidos } = instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_SUPERVISOR },
      "GET /api/v1/red-mulas/6033-9001/sankey": () => ({ cuerpo: respuestaSankey(marcada ? ["6033-9001", MULA] : ["6033-9001"]) }),
      "POST /api/v1/listas-riesgo": () => {
        marcada = true;
        return { estado: 201, cuerpo: { creada: true, entrada: { _id: "RSK-009", cuenta: MULA, motivo: "x", fecha_reporte: "2026-11-23T00:00:00Z", vigente: true } } };
      },
    });
    const usuario = userEvent.setup();
    montar();
    await screen.findByRole("img", { name: /4 transferencias/ });
    expect(nodo(MULA)).toHaveAttribute("data-en-lista", "no");
    // La victima nunca tiene boton: solo las dos propuestas sin marca.
    expect(screen.getAllByRole("button", { name: "Marcar como mula" })).toHaveLength(3);

    const campo = screen.getByLabelText(`Motivo para marcar ${MULA}`);
    await usuario.type(campo, "corto");
    await usuario.clear(campo);
    await usuario.type(campo, "abc");
    await usuario.click(within(campo.closest("form") as HTMLElement).getByRole("button", { name: "Marcar como mula" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El motivo es obligatorio: entre 5 y 200 caracteres. No se marcó la cuenta 8455-1177.");
    expect(pedidos.some((p) => p.metodo === "POST")).toBe(false);

    await usuario.type(campo, "defgh");
    await usuario.click(within(campo.closest("form") as HTMLElement).getByRole("button", { name: "Marcar como mula" }));
    const dialogo = await screen.findByRole("dialog", { name: "Marcar como mula" });
    expect(dialogo).toHaveTextContent("Se marca la cuenta 8455-1177 como mula");
    expect(pedidos.some((p) => p.metodo === "POST")).toBe(false);

    await usuario.click(within(dialogo).getByRole("button", { name: "Marcar como mula" }));

    await waitFor(() => expect(nodo(MULA)).toHaveAttribute("data-en-lista", "si"));
    expect(pedidos.find((p) => p.metodo === "POST")?.cuerpo).toEqual({ cuenta: MULA, motivo: "abcdefgh" });
    expect(screen.getByRole("status")).toHaveTextContent("La cuenta 8455-1177 quedó en lista de riesgo (RSK-009).");
    expect(document.querySelector(`#sankey-cuentas li[data-cuenta="${MULA}"]`)).toHaveTextContent("En lista de riesgo");
    expect(screen.getAllByRole("button", { name: "Marcar como mula" })).toHaveLength(2);
  });

  it("cancelar el dialogo no escribe nada", async () => {
    const { pedidos } = instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_SUPERVISOR },
      "GET /api/v1/red-mulas/6033-9001/sankey": { cuerpo: respuestaSankey() },
    });
    const usuario = userEvent.setup();
    montar();
    await screen.findByRole("img", { name: /4 transferencias/ });

    const campo = screen.getByLabelText(`Motivo para marcar ${MULA}`);
    await usuario.type(campo, "Recibio el dinero");
    await usuario.click(within(campo.closest("form") as HTMLElement).getByRole("button", { name: "Marcar como mula" }));
    const dialogo = await screen.findByRole("dialog");
    await usuario.click(within(dialogo).getByRole("button", { name: "Cancelar" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(pedidos.some((p) => p.metodo === "POST")).toBe(false);
  });

  it("muestra el error del servidor al marcar, sin dar la cuenta por marcada", async () => {
    instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_SUPERVISOR },
      "GET /api/v1/red-mulas/6033-9001/sankey": { cuerpo: respuestaSankey() },
      "POST /api/v1/listas-riesgo": errorApi(403, "prohibido", "No tienes permiso para esta acción"),
    });
    const usuario = userEvent.setup();
    montar();
    await screen.findByRole("img", { name: /4 transferencias/ });

    const campo = screen.getByLabelText(`Motivo para marcar ${MULA}`);
    await usuario.type(campo, "Recibio el dinero");
    await usuario.click(within(campo.closest("form") as HTMLElement).getByRole("button", { name: "Marcar como mula" }));
    await usuario.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Marcar como mula" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No tienes permiso para esta acción");
    expect(nodo(MULA)).toHaveAttribute("data-en-lista", "no");
  });

  it("sin salidas dice el motivo en vez de dibujar", async () => {
    instalarApiFalsa({
      "GET /api/v1/sesion": { cuerpo: SESION_AGENTE },
      "GET /api/v1/red-mulas/6033-9001/sankey": { cuerpo: { cuenta: "6033-9001", sankey: null, motivo: "Esta cuenta no tiene salidas registradas en la ventana." } },
    });
    montar();

    expect(await screen.findByText("Esta cuenta no tiene salidas registradas en la ventana.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });
});
