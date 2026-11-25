import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

import type { Respaldo } from "../api/tipos";
import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { Respaldos } from "./Respaldos";

function respaldo(campos: Partial<Respaldo> = {}): Respaldo {
  return {
    esquema_version: 1,
    respaldo_id: "RSP-20261124T153012Z",
    creado_utc: "2026-11-24T15:30:12Z",
    creado_por: "AGT-900",
    base: "antifraude",
    archivo: "RSP-20261124T153012Z.gz",
    tamano_bytes: 2_621_440,
    sha256: "a".repeat(64),
    herramienta: "mongodump version: 100.20.1",
    documentos_por_coleccion: { alertas: 10, transacciones: 90 },
    documentos_al_terminar: { alertas: 10, transacciones: 90 },
    verificacion: null,
    ...campos,
  };
}

function montar() {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter>
        <Respaldos />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Respaldos", () => {
  it("lista los respaldos con tamano, documentos y el comando de restaurar, sin boton de restaurar", async () => {
    instalarApiFalsa({ "GET /api/v1/respaldos": { cuerpo: { total: 1, en_curso: false, elementos: [respaldo()] } } });
    montar();

    const fila = (await screen.findByText("RSP-20261124T153012Z", { selector: "code" })).closest("tr") as HTMLElement;
    expect(within(fila).getByText("2.5 MB")).toBeInTheDocument();
    expect(within(fila).getByText("100 en 2 colecciones")).toBeInTheDocument();
    expect(within(fila).getByText("Sin verificar")).toBeInTheDocument();
    expect(within(fila).getByText("python tareas.py restaurar --archivo RSP-20261124T153012Z.gz --si")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /restaurar/i })).toBeNull();
  });

  it("Crear respaldo hace POST y vuelve a leer la lista", async () => {
    let creados: Respaldo[] = [];
    const api = instalarApiFalsa({
      "GET /api/v1/respaldos": () => ({ cuerpo: { total: creados.length, en_curso: false, elementos: creados } }),
      "POST /api/v1/respaldos": () => {
        creados = [respaldo()];
        return { estado: 201, cuerpo: respaldo() };
      },
    });
    montar();

    await screen.findByText("Todavía no hay respaldos.");
    await userEvent.click(screen.getByRole("button", { name: "Crear respaldo" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Crear respaldo" }));

    expect(await screen.findByText("RSP-20261124T153012Z", { selector: "code" })).toBeInTheDocument();
    expect(api.pedidos.filter((p) => p.metodo === "POST").map((p) => p.ruta)).toEqual(["/api/v1/respaldos"]);
  });

  it("Verificar muestra el resultado correcto y el fallido con sus diferencias", async () => {
    let actual = respaldo();
    const api = instalarApiFalsa({
      "GET /api/v1/respaldos": () => ({ cuerpo: { total: 1, en_curso: false, elementos: [actual] } }),
      "POST /api/v1/respaldos/RSP-20261124T153012Z/verificar": () => {
        actual = respaldo({
          verificacion: {
            verificado_utc: "2026-11-24T16:00:00Z",
            verificado_por: "AGT-900",
            resultado: "fallida",
            base_temporal: "antifraude_verificacion",
            base_temporal_borrada: true,
            duracion_segundos: 3.2,
            documentos_restaurados: { alertas: 10, transacciones: 80 },
            diferencias: [{ coleccion: "transacciones", esperados_desde: 90, esperados_hasta: 90, restaurados: 80 }],
            detalle: null,
          },
        });
        return { cuerpo: actual };
      },
    });
    montar();

    await userEvent.click(await screen.findByRole("button", { name: "Verificar RSP-20261124T153012Z" }));

    expect(await screen.findByText("Fallida", { selector: ".insignia" })).toBeInTheDocument();
    expect(screen.getByText(/se esperaban 90 y se restauraron 80/)).toBeInTheDocument();
    expect(api.pedidos.some((p) => p.metodo === "POST" && p.ruta.endsWith("/verificar"))).toBe(true);
  });

  it("con una operacion en curso deshabilita Crear y Verificar", async () => {
    instalarApiFalsa({
      "GET /api/v1/respaldos": { cuerpo: { total: 1, en_curso: true, elementos: [respaldo()] } },
    });
    montar();
    await screen.findByText("RSP-20261124T153012Z", { selector: "code" });
    expect(screen.getAllByRole("button", { name: "Crear respaldo" })[0]).toBeDisabled();
    expect(screen.getByRole("button", { name: "Verificar RSP-20261124T153012Z" })).toBeDisabled();
  });

  it("un 409 al crear muestra el mensaje del servidor", async () => {
    instalarApiFalsa({
      "GET /api/v1/respaldos": { cuerpo: { total: 0, en_curso: false, elementos: [] } },
      "POST /api/v1/respaldos": errorApi(409, "conflicto", "ya hay un respaldo o una verificacion en curso"),
    });
    montar();
    await screen.findByText("Todavía no hay respaldos.");
    await userEvent.click(screen.getByRole("button", { name: "Crear respaldo" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Crear respaldo" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("ya hay un respaldo o una verificacion en curso"));
  });
});
