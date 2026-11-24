import { filtrosVacios, leerFiltros, parametrosDeApi, validarFiltros } from "./busqueda";

function filtros(campos: Record<string, string>) {
  return { ...filtrosVacios(), ...campos };
}

describe("filtros de la busqueda forense", () => {
  it("lee la URL y deja vacio lo que no viene", () => {
    const leidos = leerFiltros(new URLSearchParams("cuenta=%20CTA-001%20&monto_min=5000&canal="));
    expect(leidos.cuenta).toBe("CTA-001");
    expect(leidos.monto_min).toBe("5000");
    expect(leidos.canal).toBe("");
    expect(leidos.desde).toBe("");
  });

  it("manda a la API solo los filtros con valor, con las fechas como dias de Costa Rica", () => {
    expect(parametrosDeApi(filtros({ desde: "2026-10-01", hasta: "2026-10-05", monto_min: "30000", estado_alerta: "nueva" }))).toEqual({
      desde: "2026-10-01T00:00:00-06:00",
      hasta: "2026-10-05T23:59:59.999999-06:00",
      monto_min: "30000",
      estado_alerta: "nueva",
    });
    expect(parametrosDeApi(filtrosVacios())).toEqual({});
  });

  it("acepta filtros coherentes", () => {
    expect(validarFiltros(filtros({ desde: "2026-10-01", hasta: "2026-10-01", monto_min: "0", monto_max: "9", canal: "sinpe_movil" }))).toEqual([]);
  });

  it("rechaza montos con decimales, fechas imposibles y valores desconocidos", () => {
    const errores = validarFiltros(filtros({ monto_max: "9.5", monto_min: "-1", desde: "2026-02-30", hasta: "ayer", canal: "tarjeta", estado_alerta: "x" }));
    expect(errores.map((e) => e.campo).sort()).toEqual(["canal", "desde", "estado_alerta", "hasta", "monto_max", "monto_min"]);
  });

  it("rechaza rangos invertidos", () => {
    expect(validarFiltros(filtros({ desde: "2026-10-05", hasta: "2026-10-01" }))[0]?.campo).toBe("desde");
    expect(validarFiltros(filtros({ monto_min: "100", monto_max: "10" }))[0]?.campo).toBe("monto_min");
  });
});
