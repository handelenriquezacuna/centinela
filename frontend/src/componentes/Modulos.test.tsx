import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import { instalarApiFalsa } from "../pruebas/apiFalsa";
import { SESION_AGENTE, SESION_SUPERVISOR } from "../pruebas/datos";
import { AvisoRegion } from "./AvisoRegion";
import { EncabezadoPagina } from "./EncabezadoPagina";
import { FechaCR } from "./FechaCR";
import { FiltroBarra } from "./FiltroBarra";
import { FormularioCampo } from "./FormularioCampo";
import { Insignia } from "./Insignia";
import { MarcoPagina } from "./MarcoPagina";
import { MontoColones } from "./MontoColones";
import { Permitido } from "./Permitido";
import { TablaDatos, type Columna, type Orden } from "./TablaDatos";
import { TarjetaMetrica } from "./TarjetaMetrica";
import { VistaError } from "./VistaError";
import { VistaVacia } from "./VistaVacia";

interface Fila {
  id: string;
  nombre: string;
}
const COLUMNAS: Columna<Fila>[] = [
  { clave: "id", titulo: "Id", celda: (f) => f.id, ordenable: true },
  { clave: "nombre", titulo: "Nombre", celda: (f) => f.nombre },
];
const FILAS: Fila[] = [
  { id: "A-1", nombre: "Uno" },
  { id: "A-2", nombre: "Dos" },
];

describe("TablaDatos", () => {
  it("cargando se anuncia como estado", () => {
    render(<TablaDatos leyenda="Filas" columnas={COLUMNAS} filas={[]} clave={(f) => f.id} estado="cargando" />);
    expect(screen.getByRole("status")).toHaveTextContent("Cargando");
  });

  it("con error muestra el mensaje y reintenta", async () => {
    const reintentar = vi.fn();
    render(
      <TablaDatos
        leyenda="Filas"
        columnas={COLUMNAS}
        filas={[]}
        clave={(f) => f.id}
        estado="error"
        error={{ texto: "La base no respondió", detalles: [] }}
        alReintentar={reintentar}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("La base no respondió");
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(reintentar).toHaveBeenCalledOnce();
  });

  it("sin filas muestra la vista vacia indicada, o una por omision", () => {
    const { rerender } = render(
      <TablaDatos leyenda="Filas" columnas={COLUMNAS} filas={[]} clave={(f) => f.id} estado="listo" vacia={<VistaVacia titulo="Sin copias" />} />,
    );
    expect(screen.getByText("Sin copias")).toBeInTheDocument();
    rerender(<TablaDatos leyenda="Filas" columnas={COLUMNAS} filas={[]} clave={(f) => f.id} estado="listo" />);
    expect(screen.getByText("No hay resultados")).toBeInTheDocument();
  });

  it("dibuja leyenda, encabezados y filas, con la etiqueta de cada celda para el telefono", () => {
    render(<TablaDatos leyenda="Filas de prueba" columnas={COLUMNAS} filas={FILAS} clave={(f) => f.id} estado="listo" />);
    const tabla = screen.getByRole("table", { name: "Filas de prueba" });
    expect(within(tabla).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Id", "Nombre"]);
    expect(within(tabla).getByText("Uno")).toHaveAttribute("data-etiqueta", "Nombre");
  });

  it("ordena por una columna ordenable y marca aria-sort", async () => {
    function Con() {
      const [orden, setOrden] = useState<Orden>({ columna: "id", sentido: "asc" });
      return <TablaDatos leyenda="Filas" columnas={COLUMNAS} filas={FILAS} clave={(f) => f.id} estado="listo" orden={orden} alOrdenar={setOrden} />;
    }
    render(<Con />);
    expect(screen.getByRole("columnheader", { name: /Id/ })).toHaveAttribute("aria-sort", "ascending");
    await userEvent.click(screen.getByRole("button", { name: /Id/ }));
    expect(screen.getByRole("columnheader", { name: /Id/ })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("columnheader", { name: "Nombre" })).not.toHaveAttribute("aria-sort");
  });

  it("pagina con tope: anterior y siguiente respetan los extremos", async () => {
    const cambiar = vi.fn();
    render(
      <TablaDatos
        leyenda="Filas"
        columnas={COLUMNAS}
        filas={FILAS}
        clave={(f) => f.id}
        estado="listo"
        paginacion={{ desplazamiento: 0, limite: 2, total: 5 }}
        alCambiarPagina={cambiar}
      />,
    );
    expect(screen.getByText("1 a 2 de 5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Anterior" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    expect(cambiar).toHaveBeenCalledWith(2);
  });
});

describe("Permitido", () => {
  function montar(sesion: typeof SESION_AGENTE) {
    instalarApiFalsa({ "GET /api/v1/sesion": { cuerpo: sesion } });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Permitido permiso="tablero" alternativa={<span>sin permiso</span>}>
          <span>con permiso</span>
        </Permitido>
      </QueryClientProvider>,
    );
  }

  it("muestra el contenido a quien tiene el permiso", async () => {
    montar(SESION_SUPERVISOR);
    expect(await screen.findByText("con permiso")).toBeInTheDocument();
  });

  it("muestra la alternativa a quien no lo tiene", async () => {
    montar(SESION_AGENTE);
    expect(await screen.findByText("sin permiso")).toBeInTheDocument();
    expect(screen.queryByText("con permiso")).not.toBeInTheDocument();
  });
});

describe("formatos de presentacion", () => {
  it("MontoColones muestra colones enteros y nada si no es un entero", () => {
    const { container, rerender } = render(<MontoColones valor={750000} />);
    expect(container).toHaveTextContent("₡750.000");
    rerender(<MontoColones valor={12.5} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("FechaCR convierte UTC a hora de Costa Rica y conserva el instante", () => {
    render(<FechaCR valor="2026-10-15T04:47:00Z" formato="con_zona" />);
    const hora = screen.getByText("14/10/2026 22:47 (hora CR)");
    expect(hora).toHaveAttribute("datetime", "2026-10-15T04:47:00Z");
  });

  it("FechaCR no dibuja una fecha invalida", () => {
    const { container } = render(<FechaCR valor="no es fecha" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Insignia", () => {
  it("la severidad lleva texto y forma, no solo color", () => {
    const { container } = render(<Insignia tipo="severidad" valor="CRITICA" />);
    expect(container.querySelector(".insignia.es-critica")).toHaveTextContent("Crítica");
    expect(container.querySelector(".icono-severidad")).toHaveAttribute("aria-hidden", "true");
  });

  it("el estado usa la clase del estado y su rotulo", () => {
    const { container } = render(<Insignia tipo="estado" valor="en_revision" />);
    expect(container.querySelector(".insignia-estado.es-en_revision")).toHaveTextContent("En revisión");
  });
});

describe("VistaVacia, VistaError y AvisoRegion", () => {
  it("VistaVacia muestra titulo, texto y accion", () => {
    render(<VistaVacia titulo="Nada aquí" texto="Todavía no hay datos." accion={<button type="button">Crear</button>} />);
    expect(screen.getByText("Nada aquí")).toBeInTheDocument();
    expect(screen.getByText("Todavía no hay datos.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Crear" })).toBeInTheDocument();
  });

  it("VistaError lista los detalles", () => {
    render(<VistaError error={{ texto: "No se pudo guardar", detalles: ["umbral: debe ser entero"] }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("umbral: debe ser entero");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("AvisoRegion anuncia el exito con status y el fallo con alert", () => {
    const { rerender } = render(<AvisoRegion resultado={{ tipo: "ok", texto: "Guardado" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Guardado");
    rerender(<AvisoRegion resultado={{ tipo: "error", error: { texto: "Falló", detalles: [] } }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Falló");
    rerender(<AvisoRegion resultado={null} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("TarjetaMetrica", () => {
  it("con datos muestra la cifra, el rotulo y la nota", () => {
    render(<TarjetaMetrica rotulo="Pendientes" valor="23" nota="la más antigua hace 2 h" />);
    expect(screen.getByText("23")).toBeInTheDocument();
    expect(screen.getByText("la más antigua hace 2 h")).toBeInTheDocument();
  });

  it("sin datos muestra un guion en vez de la cifra", () => {
    render(<TarjetaMetrica rotulo="Tasa" valor="sin resueltas" estado="sin_datos" />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("sin resueltas")).not.toBeInTheDocument();
  });
});

describe("FormularioCampo", () => {
  it("enlaza etiqueta, ayuda y error con el control", () => {
    render(
      <FormularioCampo etiqueta="Umbral" ayuda="Entre 0 y 100" error="Debe ser entero">
        <input type="number" />
      </FormularioCampo>,
    );
    const campo = screen.getByLabelText("Umbral");
    expect(campo).toHaveAttribute("aria-invalid", "true");
    expect(campo).toHaveAccessibleDescription("Entre 0 y 100 Debe ser entero");
  });

  it("sin error no marca el campo como invalido", () => {
    render(
      <FormularioCampo etiqueta="Cuenta">
        <input />
      </FormularioCampo>,
    );
    expect(screen.getByLabelText("Cuenta")).not.toHaveAttribute("aria-invalid");
  });
});

describe("FiltroBarra", () => {
  it("Enter y Buscar disparan la busqueda; Limpiar la reinicia", async () => {
    const buscar = vi.fn();
    const limpiar = vi.fn();
    render(
      <FiltroBarra rotulo="Filtros" alBuscar={buscar} alLimpiar={limpiar}>
        <FormularioCampo etiqueta="Cuenta">
          <input />
        </FormularioCampo>
      </FiltroBarra>,
    );
    await userEvent.type(screen.getByLabelText("Cuenta"), "CTA-001{Enter}");
    expect(buscar).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Limpiar" }));
    expect(limpiar).toHaveBeenCalledOnce();
    expect(screen.getByRole("search", { name: "Filtros" })).toBeInTheDocument();
  });
});

describe("MarcoPagina y EncabezadoPagina", () => {
  it("la region se nombra con el titulo y las migas enlazan", () => {
    render(
      <MemoryRouter>
        <MarcoPagina id="demo">
          <EncabezadoPagina id="demo" titulo="Demo" descripcion="Una pantalla" migas={[{ rotulo: "Panel", a: "/panel" }, { rotulo: "Demo" }]} acciones={<button type="button">Actualizar</button>} />
        </MarcoPagina>
      </MemoryRouter>,
    );
    expect(screen.getByRole("region", { name: "Demo" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Demo" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Panel" })).toHaveAttribute("href", "/panel");
    expect(screen.getByRole("button", { name: "Actualizar" })).toBeInTheDocument();
  });
});
