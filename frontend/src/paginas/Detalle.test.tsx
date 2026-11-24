import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";

import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { SESION_AGENTE } from "../pruebas/datos";
import { detalleDeAlerta } from "../pruebas/datosDetalle";
import { respuestaSankey } from "../pruebas/datosRed";
import { Detalle } from "./Detalle";

function montar() {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter initialEntries={["/detalle/ALR-001"]}>
        <Routes>
          <Route path="/detalle/:alertaId" element={<Detalle />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const BASE = {
  "GET /api/v1/sesion": { cuerpo: SESION_AGENTE },
  "GET /api/v1/red-mulas/6033-9001/sankey": { cuerpo: respuestaSankey() },
};

describe("Detalle de alerta", () => {
  it("explica cada regla con su evidencia y marca el corte alcanzado", async () => {
    instalarApiFalsa({ ...BASE, "GET /api/v1/alertas/ALR-001/detalle": { cuerpo: detalleDeAlerta() } });
    montar();

    expect(await screen.findByRole("heading", { name: "Alerta ALR-001" })).toBeInTheDocument();
    const tabla = document.querySelector("#tabla-aportes") as HTMLElement;
    expect(within(tabla).getByText("Monto 7 veces el promedio del cliente")).toBeInTheDocument();
    expect(within(tabla).getByText("Evidencia: ₡280.000 contra un promedio de ₡40.000")).toBeInTheDocument();
    expect(within(tabla).getByText("+35")).toBeInTheDocument();
    expect(document.querySelector("#suma-aportes")).toHaveTextContent("75");
    expect(document.querySelector("#puntaje-total")).toHaveTextContent("75 → severidad crítica");
    const cortes = document.querySelector("#tabla-cortes") as HTMLElement;
    expect(within(cortes).getByText("corte alcanzado (75)").closest("tr")).toHaveClass("corte-alcanzado");
    expect(screen.getByText("Los cortes de abajo son los de la política usada por la alerta.")).toBeInTheDocument();
  });

  it("muestra colones enteros y hora de Costa Rica, con el recorrido del dinero", async () => {
    instalarApiFalsa({ ...BASE, "GET /api/v1/alertas/ALR-001/detalle": { cuerpo: detalleDeAlerta() } });
    montar();

    await screen.findByRole("heading", { name: "Alerta ALR-001" });
    expect(screen.getByText("₡280.000", { selector: "dd.monto" })).toBeInTheDocument();
    expect(screen.getByText("24/09/2026 22:00", { selector: "dd.hora" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ver red de mulas" })).toHaveAttribute(
      "href",
      "/red-mulas/6033-9001?desde=2026-09-25T04%3A00%3A00Z",
    );
    expect(await screen.findByRole("img", { name: /4 transferencias/ })).toBeInTheDocument();
  });

  it("toma la alerta sin confirmar y manda el estado que vio", async () => {
    let tomada = false;
    const { pedidos } = instalarApiFalsa({
      ...BASE,
      "GET /api/v1/alertas/ALR-001/detalle": () => ({ cuerpo: detalleDeAlerta({}, tomada ? "en_revision" : "nueva") }),
      "POST /api/v1/alertas/ALR-001/transiciones": () => {
        tomada = true;
        return { cuerpo: { alerta: detalleDeAlerta({}, "en_revision").alerta, caso_id: null } };
      },
    });
    const usuario = userEvent.setup();
    montar();

    await usuario.click(await screen.findByRole("button", { name: "Tomar" }));

    await waitFor(() => expect(document.querySelector("#insignia-estado")).toHaveClass("es-en_revision"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(pedidos.find((p) => p.metodo === "POST")?.cuerpo).toEqual({ destino: "en_revision", nota: "", estado_esperado: "nueva" });
    expect(screen.getByRole("status")).toHaveTextContent("Listo: la alerta pasó a En revisión");
    // Ya no se puede tomar de nuevo; ahora ofrece cerrarla.
    expect(screen.queryByRole("button", { name: "Tomar" })).toBeNull();
    expect(screen.getByRole("button", { name: "Confirmar fraude" })).toBeInTheDocument();
  });

  it("pide confirmar en un dialogo antes de cerrar la alerta, con la nota en el historial", async () => {
    let confirmada = false;
    const { pedidos } = instalarApiFalsa({
      ...BASE,
      "GET /api/v1/alertas/ALR-001/detalle": () => ({ cuerpo: detalleDeAlerta({}, confirmada ? "confirmada" : "en_revision") }),
      "POST /api/v1/alertas/ALR-001/transiciones": () => {
        confirmada = true;
        return { cuerpo: { alerta: detalleDeAlerta({}, "confirmada").alerta, caso_id: null } };
      },
    });
    const usuario = userEvent.setup();
    montar();

    await usuario.type(await screen.findByLabelText("Nota (se agrega al historial)"), "Cliente confirma la llamada");
    await usuario.click(screen.getByRole("button", { name: "Confirmar fraude" }));
    const dialogo = await screen.findByRole("dialog", { name: "Confirmar fraude: confirmar" });
    expect(dialogo).toHaveTextContent("Esta acción cierra la alerta ALR-001 y no se puede deshacer.");
    expect(pedidos.some((p) => p.metodo === "POST")).toBe(false);

    await usuario.click(within(dialogo).getByRole("button", { name: "Confirmar fraude" }));

    await waitFor(() => expect(document.querySelector("#insignia-estado")).toHaveClass("es-confirmada"));
    expect(pedidos.find((p) => p.metodo === "POST")?.cuerpo).toEqual({
      destino: "confirmada",
      nota: "Cliente confirma la llamada",
      estado_esperado: "en_revision",
    });
    expect(screen.queryByRole("button", { name: "Confirmar fraude" })).toBeNull();
    expect(screen.getByText(/es un estado terminal y no admite más acciones/)).toBeInTheDocument();
  });

  it("cancelar el dialogo deja la alerta como estaba", async () => {
    const { pedidos } = instalarApiFalsa({ ...BASE, "GET /api/v1/alertas/ALR-001/detalle": { cuerpo: detalleDeAlerta({}, "en_revision") } });
    const usuario = userEvent.setup();
    montar();

    await usuario.click(await screen.findByRole("button", { name: "Descartar" }));
    await usuario.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancelar" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(pedidos.some((p) => p.metodo === "POST")).toBe(false);
  });

  it("si otro la tomo (409) lo dice claro y vuelve a leer el estado vigente", async () => {
    let lecturas = 0;
    instalarApiFalsa({
      ...BASE,
      "GET /api/v1/alertas/ALR-001/detalle": () => {
        lecturas += 1;
        return { cuerpo: detalleDeAlerta({}, lecturas === 1 ? "nueva" : "en_revision") };
      },
      "POST /api/v1/alertas/ALR-001/transiciones": errorApi(409, "conflicto", "La alerta ALR-001 ya no esta en nueva"),
    });
    const usuario = userEvent.setup();
    montar();

    await usuario.click(await screen.findByRole("button", { name: "Tomar" }));

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("otro agente la tomó o resolvió");
    expect(alerta).toHaveTextContent("ya no esta en nueva");
    await waitFor(() => expect(document.querySelector("#insignia-estado")).toHaveClass("es-en_revision"));
    expect(lecturas).toBe(2);
  });

  it("una alerta v1 avisa que no trae aporte por regla", async () => {
    instalarApiFalsa({
      ...BASE,
      "GET /api/v1/alertas/ALR-001/detalle": {
        cuerpo: detalleDeAlerta({ es_v2: false, aviso_v1: "Alerta v1: no registra el aporte de cada regla." }),
      },
    });
    montar();

    expect(await screen.findByText("Alerta v1: no registra el aporte de cada regla.")).toBeInTheDocument();
    expect(document.querySelector("#tabla-aportes")).toBeNull();
  });

  it("una alerta inexistente dice que no existe", async () => {
    instalarApiFalsa({ ...BASE, "GET /api/v1/alertas/ALR-001/detalle": errorApi(404, "no_encontrado", "No existe la alerta ALR-001") });
    montar();

    expect(await screen.findByRole("alert")).toHaveTextContent("No existe la alerta ALR-001.");
  });
});
