import casos from "./formatos.casos.json";
import { colones, decimal, etiqueta, horaCostaRica, porcentaje } from "./formatos";

describe("formatos de Costa Rica, con los mismos casos que app/api/formatos.py", () => {
  it.each(casos.colones)("colones($entrada) = $salida", ({ entrada, salida }) => {
    expect(colones(entrada)).toBe(salida);
  });

  it.each(casos.hora_costa_rica)("horaCostaRica($entrada) = $salida", ({ entrada, salida }) => {
    expect(horaCostaRica(entrada)).toBe(salida);
  });

  it.each(casos.decimal)("decimal($entrada) = $salida", ({ entrada, salida }) => {
    expect(decimal(entrada)).toBe(salida);
  });

  it.each(casos.porcentaje)("porcentaje($entrada) = $salida", ({ entrada, salida }) => {
    expect(porcentaje(entrada)).toBe(salida);
  });

  it.each(casos.etiqueta)("etiqueta($entrada) = $salida", ({ entrada, salida }) => {
    expect(etiqueta(entrada)).toBe(salida);
  });

  it("nunca muestra centimos ni redondea un monto que no es entero", () => {
    expect(colones(999.99)).toBe("");
    expect(colones(Number.MAX_SAFE_INTEGER + 2)).toBe("");
  });
});
