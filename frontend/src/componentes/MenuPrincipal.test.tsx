import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

import { SESION_ADMINISTRADOR, SESION_AGENTE, SESION_SUPERVISOR } from "../pruebas/datos";
import type { SesionActual } from "../api/tipos";
import { agruparPorArea, MenuPrincipal } from "./MenuPrincipal";

function montar(sesion: SesionActual, ruta = "/panel") {
  render(
    <MemoryRouter initialEntries={[ruta]}>
      <MenuPrincipal sesion={sesion} />
    </MemoryRouter>,
  );
  return screen.getByRole("navigation", { name: "Navegación principal" });
}

describe("MenuPrincipal por rol", () => {
  it("el agente ve solo Alertas", () => {
    const nav = montar(SESION_AGENTE);
    expect(within(nav).getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual(["Alertas"]);
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Panel", "Búsqueda", "Red de mulas"]);
  });

  it("el supervisor suma Supervision, sin Administracion", () => {
    const nav = montar(SESION_SUPERVISOR);
    expect(within(nav).getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual(["Alertas", "Supervisión"]);
    expect(within(nav).getByRole("link", { name: "Reglas y política" })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Respaldos" })).not.toBeInTheDocument();
  });

  it("el administrador ve ademas Administracion, con Respaldos y Usuarios", () => {
    const nav = montar(SESION_ADMINISTRADOR);
    expect(within(nav).getAllByRole("group").map((g) => g.getAttribute("aria-label"))).toEqual(["Alertas", "Supervisión", "Administración"]);
    expect(within(nav).getAllByRole("link").map((a) => a.textContent).slice(-3)).toEqual(["Salud del sistema", "Respaldos", "Usuarios del portal"]);
  });

  it("marca la pantalla actual", () => {
    const nav = montar(SESION_SUPERVISOR, "/tablero");
    expect(within(nav).getByRole("link", { name: "Tablero" })).toHaveAttribute("aria-current", "page");
  });
});

describe("menu en telefono", () => {
  it("el boton Menu abre y cierra, y Esc lo cierra", async () => {
    montar(SESION_AGENTE);
    const boton = screen.getByRole("button", { name: "Menú" });
    expect(boton).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(boton);
    expect(boton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("navigation", { name: "Navegación principal" })).toHaveClass("menu-abierto");
    await userEvent.keyboard("{Escape}");
    expect(boton).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(boton);
    await userEvent.click(boton);
    expect(boton).toHaveAttribute("aria-expanded", "false");
  });

  it("al elegir una pantalla el menu se cierra", async () => {
    montar(SESION_AGENTE);
    const boton = screen.getByRole("button", { name: "Menú" });
    await userEvent.click(boton);
    await userEvent.click(screen.getByRole("link", { name: "Búsqueda" }));
    expect(boton).toHaveAttribute("aria-expanded", "false");
  });
});

describe("agruparPorArea", () => {
  it("conserva el orden del servidor", () => {
    const grupos = agruparPorArea(SESION_ADMINISTRADOR.navegacion);
    expect(grupos.map(([area]) => area)).toEqual(["Alertas", "Supervisión", "Administración"]);
    expect(grupos[0]?.[1].map((p) => p.ruta)).toEqual(["/panel", "/busqueda", "/red-mulas"]);
  });
});
