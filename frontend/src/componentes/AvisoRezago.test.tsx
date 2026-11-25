import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";

import type { AvisoRezago as Aviso } from "../api/tipos";
import { instalarApiFalsa } from "../pruebas/apiFalsa";
import { AvisoRezago } from "./AvisoRezago";

const AL_DIA: Aviso = {
  atrasada: false,
  por_conteo: false,
  pendientes: 0,
  aviso_desde: 500,
  atraso_segundos: 0,
  aviso_atraso_desde: 15,
  atraso_legible: "",
  segundos_para_ponerse_al_dia: null,
  estimado: "",
  medido_en: null,
};

function montar(aviso: Aviso) {
  instalarApiFalsa({ "GET /api/v1/deteccion/rezago": { cuerpo: aviso } });
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <AvisoRezago />
    </QueryClientProvider>,
  );
}

describe("AvisoRezago", () => {
  it("al dia deja la region accesible vacia", async () => {
    montar(AL_DIA);

    expect(await screen.findByRole("status")).toBeEmptyDOMElement();
  });

  it("con conteo dice las pendientes y el estimado", async () => {
    montar({ ...AL_DIA, atrasada: true, por_conteo: true, pendientes: 9988, estimado: "2 min" });

    const aviso = await screen.findByText(/Detección atrasada/);

    expect(aviso.closest("[role=status]")).toHaveTextContent(/9\D?988 transacciones por evaluar, unos 2 min para ponerse al día/);
  });

  it("solo con atraso dice cuanto va detras de la ingesta, sin inventar un conteo", async () => {
    montar({ ...AL_DIA, atrasada: true, por_conteo: false, atraso_segundos: 125, atraso_legible: "2 min" });

    const aviso = await screen.findByText(/Detección atrasada/);
    const region = aviso.closest("[role=status]");

    expect(region).toHaveTextContent("Detección atrasada: va unos 2 min detrás de la ingesta");
    expect(region).not.toHaveTextContent("transacciones por evaluar");
  });
});
