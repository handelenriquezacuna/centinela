import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import { ACCIONES_EN_REVISION, ACCIONES_NUEVA } from "../pruebas/datosDetalle";
import { BarraDecision } from "./BarraDecision";

function montar(propiedades: Partial<React.ComponentProps<typeof BarraDecision>> = {}) {
  const alResolver = vi.fn();
  const resultado = render(
    <MemoryRouter>
      <BarraDecision
        alertaId="ALR-001"
        estado="nueva"
        severidad="CRITICA"
        acciones={ACCIONES_NUEVA}
        alResolver={alResolver}
        siguiente="ALR-002"
        {...propiedades}
      />
    </MemoryRouter>,
  );
  return { alResolver, ...resultado };
}

describe("BarraDecision", () => {
  it("muestra el estado, la severidad y solo las acciones que manda el servidor", () => {
    montar();
    const barra = screen.getByRole("region", { name: "Decisión sobre la alerta" });
    expect(within(barra).getByText("Nueva")).toBeInTheDocument();
    expect(within(barra).getByText("Severidad crítica")).toBeInTheDocument();
    expect(within(barra).getByRole("button", { name: "Tomar" })).toBeInTheDocument();
    expect(within(barra).getByRole("button", { name: "Descartar" })).toBeInTheDocument();
    expect(within(barra).queryByRole("button", { name: "Confirmar fraude" })).toBeNull();
  });

  it("una accion que no cierra se aplica con un clic y lleva la nota", async () => {
    const usuario = userEvent.setup();
    const { alResolver } = montar();

    await usuario.type(screen.getByLabelText("Nota (se agrega al historial)"), " Llamada al cliente ");
    await usuario.click(screen.getByRole("button", { name: "Tomar" }));

    expect(alResolver).toHaveBeenCalledWith(ACCIONES_NUEVA[0], "Llamada al cliente");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("una accion que cierra pide confirmar; cancelar no resuelve nada", async () => {
    const usuario = userEvent.setup();
    const { alResolver } = montar({ estado: "en_revision", acciones: ACCIONES_EN_REVISION });

    await usuario.click(screen.getByRole("button", { name: "Confirmar fraude" }));
    const dialogo = await screen.findByRole("dialog", { name: "Confirmar fraude: confirmar" });
    expect(dialogo).toHaveTextContent("cierra la alerta ALR-001 y no se puede deshacer");
    await usuario.click(within(dialogo).getByRole("button", { name: "Cancelar" }));
    expect(alResolver).not.toHaveBeenCalled();

    await usuario.click(screen.getByRole("button", { name: "Confirmar fraude" }));
    await usuario.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirmar fraude" }));
    expect(alResolver).toHaveBeenCalledTimes(1);
  });

  it("ofrece la siguiente alerta; sin ninguna lo dice", () => {
    const { rerender } = montar();
    expect(screen.getByRole("link", { name: "Siguiente alerta" })).toHaveAttribute("href", "/detalle/ALR-002");

    rerender(
      <MemoryRouter>
        <BarraDecision alertaId="ALR-001" estado="nueva" severidad="ALTA" acciones={ACCIONES_NUEVA} alResolver={vi.fn()} siguiente={null} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("link", { name: "Siguiente alerta" })).toBeNull();
    expect(screen.getByText("No hay más alertas abiertas en la cola.")).toBeInTheDocument();
  });

  it("una alerta cerrada no ofrece acciones y deja la siguiente como salida principal", () => {
    montar({ estado: "confirmada", acciones: [] });
    expect(screen.queryByRole("button", { name: "Tomar" })).toBeNull();
    expect(screen.getByText(/no admite más acciones/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Siguiente alerta" })).toHaveClass("boton-primario");
  });

  it("anuncia el resultado y el conflicto sin jerga", () => {
    montar({ resultado: { tipo: "error", error: { texto: "La alerta cambió antes de que aplicaras la acción", detalles: [] } } });
    expect(screen.getByRole("alert")).toHaveTextContent("La alerta cambió antes de que aplicaras la acción");
  });
});
