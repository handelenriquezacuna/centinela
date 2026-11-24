import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

import type { Evaluacion, PoliticaVigente, Regla } from "../api/tipos";
import { errorApi, instalarApiFalsa } from "../pruebas/apiFalsa";
import { SESION_AGENTE, SESION_SUPERVISOR } from "../pruebas/datos";
import { Reglas } from "./Reglas";

function regla(campos: Partial<Regla> = {}): Regla {
  return {
    _id: "REG-001",
    codigo: "R-01",
    descripcion: "Monto muy por encima del promedio",
    tipo: "monto",
    umbral: 8,
    parametros: {},
    peso: 40,
    activa: true,
    version: 1,
    esquema_version: 1,
    versiones_anteriores: [],
    ...campos,
  };
}

const POLITICA: PoliticaVigente = {
  _id: "POL-001",
  descripcion: "Cortes",
  version: 1,
  umbral_alerta: 40,
  puntaje_maximo: 100,
  cortes_severidad: [
    { severidad: "MEDIA", puntaje_minimo: 40, puntaje_maximo: 54 },
    { severidad: "ALTA", puntaje_minimo: 55, puntaje_maximo: 69 },
    { severidad: "CRITICA", puntaje_minimo: 70, puntaje_maximo: 100 },
  ],
  vigente_desde: null,
  versiones_anteriores: [],
};

const TIPOS = {
  elementos: [
    { tipo: "monto", umbral: "veces el promedio del cliente", parametros: [], evaluable: true },
    { tipo: "velocidad", umbral: "transferencias en la ventana", parametros: [{ nombre: "ventana_minutos", descripcion: "minutos" }], evaluable: true },
    { tipo: "red_mulas", umbral: "saltos", parametros: [], evaluable: false },
  ],
};

const EVALUACION: Evaluacion = {
  transaccion_id: "TXN-001",
  puntaje: 75,
  severidad: "CRITICA",
  genera_alerta: true,
  politica_id: "POL-001",
  politica_version: 1,
  reglas_disparadas: [{ codigo: "R-01", regla_id: "REG-001", version: 1, peso: 40, explicacion: "Monto 11.2 veces el promedio" }],
  reglas_no_evaluadas: [{ codigo: "R-09", regla_id: "REG-009", version: null, motivo: "sin perfil" }],
};

function montar() {
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <MemoryRouter>
        <Reglas />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function rutasBase(reglas: () => Regla[] = () => [regla()], sesion = SESION_SUPERVISOR) {
  return {
    "GET /api/v1/sesion": { cuerpo: sesion },
    "GET /api/v1/reglas": () => ({ cuerpo: { total: reglas().length, elementos: reglas() } }),
    "GET /api/v1/reglas/tipos": { cuerpo: TIPOS },
    "GET /api/v1/politica": { cuerpo: POLITICA },
  };
}

async function tarjeta(id = "REG-001") {
  return await screen.findByRole("region", { name: new RegExp(id === "REG-001" ? "R-01" : id) });
}

describe("Reglas: lectura", () => {
  it("lista politica, tipos y catalogo con estado, umbral, peso, parametros y version", async () => {
    instalarApiFalsa(
      rutasBase(() => [
        regla({ parametros: { ventana_minutos: 30 }, versiones_anteriores: [{ ...regla(), version: 1, umbral: 5, reemplazada_en: "2026-11-20T18:00:00Z" } as never], version: 2 }),
        regla({ _id: "REG-002", codigo: "R-02", descripcion: "Horario", activa: false }),
      ]),
    );
    montar();

    const primera = await tarjeta();
    expect(within(primera).getByText("versión 2")).toBeInTheDocument();
    expect(within(primera).getByText("Activa", { selector: ".insignia" })).toBeInTheDocument();
    expect(within(primera).getByText('{"ventana_minutos":30}')).toBeInTheDocument();
    expect(within(primera).getByText("Versiones anteriores (1)")).toBeInTheDocument();
    expect(within(primera).getByText("20/11/2026 12:00")).toBeInTheDocument();
    expect(await screen.findByText("Apagada", { selector: ".insignia" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Catálogo (2)" })).toBeInTheDocument();
    expect(screen.getByText("No, todavía")).toBeInTheDocument();
    await waitFor(() => expect(document.getElementById("politica-version")).toHaveTextContent("versión 1"));
    expect(document.getElementById("politica-umbral")).toHaveTextContent("40");
  });

  it("sin permiso de edicion no muestra formularios ni el simulador", async () => {
    instalarApiFalsa(rutasBase(undefined, SESION_AGENTE));
    montar();
    await tarjeta();
    await screen.findByText("Política vigente");
    expect(screen.queryByRole("button", { name: "Guardar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Simular" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Crear regla" })).toBeNull();
  });
});

describe("Reglas: edicion con concurrencia optimista", () => {
  it("guarda solo lo que cambio, manda version_esperada y muestra la version nueva", async () => {
    let actual = regla();
    const api = instalarApiFalsa({
      ...rutasBase(() => [actual]),
      "PATCH /api/v1/reglas/REG-001": (pedido) => {
        actual = regla({ ...(pedido.cuerpo as object), version: 2, versiones_anteriores: [] } as Partial<Regla>);
        return { cuerpo: actual };
      },
    });
    montar();
    const usuario = userEvent.setup();
    const t = await tarjeta();

    const umbral = within(t).getByLabelText("Umbral");
    await usuario.clear(umbral);
    await usuario.type(umbral, "20");
    await usuario.click(within(t).getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(within(t).getByText("Regla R-01 guardada: ahora está en la versión 2.")).toBeInTheDocument());
    const parche = api.pedidos.find((p) => p.metodo === "PATCH");
    expect(parche?.cuerpo).toEqual({ umbral: 20, version_esperada: 1 });
    expect(within(t).getByText("versión 2")).toBeInTheDocument();
  });

  it("un 409 dice que cambio, no escribe y recarga lo vigente", async () => {
    let vigente = regla();
    instalarApiFalsa({
      ...rutasBase(() => [vigente]),
      "PATCH /api/v1/reglas/REG-001": () => {
        vigente = regla({ umbral: 3, version: 3 });
        return { ...errorApi(409, "conflicto", "La regla REG-001 cambio mientras se editaba; volver a leerla y reintentar"), cuerpo: { error: { codigo: "conflicto", mensaje: "La regla REG-001 cambio", detalles: [{ campo: "version", mensaje: "version actual: 3" }] } } };
      },
    });
    montar();
    const usuario = userEvent.setup();
    const t = await tarjeta();

    await usuario.clear(within(t).getByLabelText("Peso (0-100)"));
    await usuario.type(within(t).getByLabelText("Peso (0-100)"), "55");
    await usuario.click(within(t).getByRole("button", { name: "Guardar" }));

    const aviso = await within(t).findByRole("alert");
    expect(aviso).toHaveTextContent("La regla R-01 cambió mientras se editaba, así que no se guardó nada");
    expect(aviso).toHaveTextContent("version: version actual: 3");
    await waitFor(() => expect(within(t).getByText("versión 3")).toBeInTheDocument());
    // El formulario se reinicia con lo vigente (umbral 3, peso 40), no con lo que se intento.
    expect(within(t).getByLabelText("Umbral")).toHaveValue(3);
    expect(within(t).getByLabelText("Peso (0-100)")).toHaveValue(40);
  });

  it("valida en el cliente: sin cambios, parametros que no son JSON o peso fuera de rango no llaman a la API", async () => {
    const api = instalarApiFalsa(rutasBase());
    montar();
    const usuario = userEvent.setup();
    const t = await tarjeta();
    const guardar = within(t).getByRole("button", { name: "Guardar" });

    await usuario.click(guardar);
    expect(await within(t).findByRole("alert")).toHaveTextContent("No hay cambios que guardar.");

    const parametros = within(t).getByLabelText("Parámetros (JSON)");
    await usuario.clear(parametros);
    await usuario.type(parametros, "no es json");
    await usuario.click(guardar);
    expect(await within(t).findByText(/los parámetros no son JSON válido/)).toBeInTheDocument();

    await usuario.clear(parametros);
    await usuario.type(parametros, "{{}");
    await usuario.clear(within(t).getByLabelText("Peso (0-100)"));
    await usuario.type(within(t).getByLabelText("Peso (0-100)"), "150");
    await usuario.click(guardar);
    expect(await within(t).findByText("El peso tiene que ser un entero de 0 a 100.")).toBeInTheDocument();
    expect(api.pedidos.some((p) => p.metodo === "PATCH")).toBe(false);
  });

  it("desactivar es una edicion mas", async () => {
    const api = instalarApiFalsa({ ...rutasBase(), "PATCH /api/v1/reglas/REG-001": { cuerpo: regla({ activa: false, version: 2 }) } });
    montar();
    const usuario = userEvent.setup();
    const t = await tarjeta();
    await usuario.selectOptions(within(t).getByLabelText("Activa"), "false");
    await usuario.click(within(t).getByRole("button", { name: "Guardar" }));
    await within(t).findByText("Regla R-01 guardada: ahora está en la versión 2.");
    expect(api.pedidos.find((p) => p.metodo === "PATCH")?.cuerpo).toEqual({ activa: false, version_esperada: 1 });
  });
});

describe("Reglas: crear", () => {
  it("crea una regla de un tipo evaluable con los parametros que ese tipo lee", async () => {
    const api = instalarApiFalsa({
      ...rutasBase(),
      "POST /api/v1/reglas": { estado: 201, cuerpo: regla({ _id: "REG-013", codigo: "R-13", descripcion: "Nueva", tipo: "velocidad", parametros: { ventana_minutos: 15 } }) },
    });
    montar();
    const usuario = userEvent.setup();
    const formulario = await screen.findByRole("form", { name: "Crear una regla" });

    // Los tipos no evaluables no se ofrecen.
    expect(within(formulario).queryByRole("option", { name: "red_mulas" })).toBeNull();
    await usuario.type(within(formulario).getByLabelText("Código"), "R-13");
    await usuario.type(within(formulario).getByLabelText("Descripción"), "Nueva");
    await usuario.selectOptions(within(formulario).getByLabelText("Tipo"), "velocidad");
    expect(within(formulario).getByLabelText("Parámetros (JSON)")).toHaveValue('{"ventana_minutos":0}');
    await usuario.clear(within(formulario).getByLabelText("Parámetros (JSON)"));
    await usuario.type(within(formulario).getByLabelText("Parámetros (JSON)"), '{{"ventana_minutos":15}');
    await usuario.click(within(formulario).getByRole("button", { name: "Crear regla" }));

    expect(await screen.findByText("Regla R-13 creada como REG-013, en la versión 1.")).toBeInTheDocument();
    expect(api.pedidos.find((p) => p.metodo === "POST")?.cuerpo).toEqual({
      codigo: "R-13",
      descripcion: "Nueva",
      tipo: "velocidad",
      umbral: 0,
      parametros: { ventana_minutos: 15 },
      peso: 10,
      activa: true,
    });
  });

  it("un codigo repetido (409) y un parametro mal escrito (422) se muestran con su detalle", async () => {
    instalarApiFalsa({
      ...rutasBase(),
      "POST /api/v1/reglas": { estado: 409, cuerpo: { error: { codigo: "conflicto", mensaje: "Ya existe una regla con el codigo R-01", detalles: [{ campo: "codigo", mensaje: "tiene que ser unico" }] } } },
    });
    montar();
    const usuario = userEvent.setup();
    const formulario = await screen.findByRole("form", { name: "Crear una regla" });
    await usuario.type(within(formulario).getByLabelText("Código"), "R-01");
    await usuario.type(within(formulario).getByLabelText("Descripción"), "Repetida");
    await usuario.click(within(formulario).getByRole("button", { name: "Crear regla" }));
    expect(await screen.findByText(/codigo: tiene que ser unico/)).toBeInTheDocument();
  });
});

describe("Reglas: simular", () => {
  it("evalua el ejemplo TXN-001 con el catalogo guardado y muestra 75 CRITICA", async () => {
    const api = instalarApiFalsa({ ...rutasBase(), "POST /api/v1/reglas/simular": { cuerpo: EVALUACION } });
    montar();
    const usuario = userEvent.setup();
    const simulador = await screen.findByRole("form", { name: "Simular una transacción" });

    await usuario.click(within(simulador).getByRole("button", { name: "Cargar el ejemplo TXN-001" }));
    await usuario.click(within(simulador).getByRole("button", { name: "Simular" }));

    await screen.findByText("Resultado para TXN-001");
    expect(document.getElementById("simulacion-puntaje")).toHaveTextContent("75");
    expect(document.getElementById("simulacion-severidad")).toHaveTextContent("CRITICA");
    expect(screen.getByText("Monto 11.2 veces el promedio")).toBeInTheDocument();
    expect(screen.getByText("R-09: sin perfil")).toBeInTheDocument();
    const cuerpo = api.pedidos.find((p) => p.ruta === "/api/v1/reglas/simular")?.cuerpo as Record<string, unknown>;
    expect(Object.keys(cuerpo).sort()).toEqual(["contexto", "transaccion"]);
    expect((cuerpo.transaccion as { _id: string })._id).toBe("TXN-001");
  });

  it("con una regla propuesta manda solo esa regla, sin su historia", async () => {
    const api = instalarApiFalsa({ ...rutasBase(), "POST /api/v1/reglas/simular": { cuerpo: { ...EVALUACION, puntaje: 40, severidad: "MEDIA" } } });
    montar();
    const usuario = userEvent.setup();
    const simulador = await screen.findByRole("form", { name: "Simular una transacción" });

    await usuario.click(within(simulador).getByRole("button", { name: "Cargar el ejemplo TXN-001" }));
    await usuario.selectOptions(within(simulador).getByLabelText("Regla propuesta"), "REG-001");
    const umbral = within(simulador).getByLabelText("Umbral propuesto");
    expect(umbral).toHaveValue(8);
    await usuario.clear(umbral);
    await usuario.type(umbral, "20");
    await usuario.click(within(simulador).getByRole("button", { name: "Simular" }));

    await screen.findByText("Resultado para TXN-001");
    expect(document.getElementById("simulacion-severidad")).toHaveTextContent("MEDIA");
    const cuerpo = api.pedidos.find((p) => p.ruta === "/api/v1/reglas/simular")?.cuerpo as { reglas: Regla[] };
    expect(cuerpo.reglas).toHaveLength(1);
    expect(cuerpo.reglas[0]).toMatchObject({ _id: "REG-001", umbral: 20, peso: 40 });
    expect(cuerpo.reglas[0]).not.toHaveProperty("versiones_anteriores");
  });

  it("un JSON roto o un 409 sin politica se dicen sin romper la pantalla", async () => {
    instalarApiFalsa({ ...rutasBase(), "POST /api/v1/reglas/simular": errorApi(409, "conflicto", "No hay politica_deteccion vigente; mandar una en `politica`") });
    montar();
    const usuario = userEvent.setup();
    const simulador = await screen.findByRole("form", { name: "Simular una transacción" });

    await usuario.type(within(simulador).getByLabelText("Transacción y contexto (JSON)"), "{{no");
    await usuario.click(within(simulador).getByRole("button", { name: "Simular" }));
    expect(await screen.findByText("La transacción y su contexto no son JSON válido.")).toBeInTheDocument();

    await usuario.click(within(simulador).getByRole("button", { name: "Cargar el ejemplo TXN-001" }));
    await usuario.click(within(simulador).getByRole("button", { name: "Simular" }));
    expect(await screen.findByText(/No hay politica_deteccion vigente/)).toBeInTheDocument();
  });
});

describe("Reglas: politica", () => {
  it("edita el umbral y los cortes y manda la politica con su version", async () => {
    const api = instalarApiFalsa({ ...rutasBase(), "PATCH /api/v1/politica": { cuerpo: { ...POLITICA, version: 2, umbral_alerta: 35 } } });
    montar();
    const usuario = userEvent.setup();
    const formulario = await screen.findByRole("form", { name: "Editar la política" });

    const umbral = within(formulario).getByLabelText("Umbral de alerta");
    await usuario.clear(umbral);
    await usuario.type(umbral, "35");
    const desde = within(formulario).getByLabelText("Desde (Media)");
    await usuario.clear(desde);
    await usuario.type(desde, "35");
    await usuario.click(within(formulario).getByRole("button", { name: "Guardar política" }));

    expect(await screen.findByText(/Política guardada: ahora está en la versión 2/)).toBeInTheDocument();
    const cuerpo = api.pedidos.find((p) => p.metodo === "PATCH")?.cuerpo as Record<string, unknown>;
    expect(cuerpo.umbral_alerta).toBe(35);
    expect(cuerpo.version_esperada).toBe(1);
    expect(cuerpo.cortes_severidad).toEqual([
      { severidad: "MEDIA", puntaje_minimo: 35, puntaje_maximo: 54 },
      { severidad: "ALTA", puntaje_minimo: 55, puntaje_maximo: 69 },
      { severidad: "CRITICA", puntaje_minimo: 70, puntaje_maximo: 100 },
    ]);
    expect(cuerpo).not.toHaveProperty("puntaje_maximo");
  });

  it("cortes con hueco (422) muestran el tramo que falla; un 409 pide revisar", async () => {
    let respuesta = {
      estado: 422,
      cuerpo: { error: { codigo: "parametros_invalidos", mensaje: "Los cortes de severidad no cubren de umbral_alerta a puntaje_maximo sin huecos ni solapes", detalles: [{ campo: "cortes_severidad.1.puntaje_minimo", mensaje: "hueco: tendria que empezar en 55" }] } },
    };
    instalarApiFalsa({ ...rutasBase(), "PATCH /api/v1/politica": () => respuesta });
    montar();
    const usuario = userEvent.setup();
    const formulario = await screen.findByRole("form", { name: "Editar la política" });
    const desde = within(formulario).getByLabelText("Desde (Alta)");
    await usuario.clear(desde);
    await usuario.type(desde, "60");
    await usuario.click(within(formulario).getByRole("button", { name: "Guardar política" }));
    expect(await screen.findByText(/cortes_severidad.1.puntaje_minimo: hueco: tendria que empezar en 55/)).toBeInTheDocument();

    respuesta = { estado: 409, cuerpo: { error: { codigo: "conflicto", mensaje: "La politica cambio", detalles: [] } } };
    await usuario.click(within(formulario).getByRole("button", { name: "Guardar política" }));
    expect(await screen.findByText(/La política cambió mientras se editaba/)).toBeInTheDocument();
  });
});
