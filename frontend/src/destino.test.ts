import casos from "./destino.casos.json";
import { destinoSeguro, rutaDeLogin } from "./destino";

describe("destinoSeguro, con los mismos casos que destino_seguro de Python", () => {
  it.each(casos.casos)("destinoSeguro($entrada) = $salida", ({ entrada, salida }) => {
    expect(destinoSeguro(entrada)).toBe(salida);
  });
});

describe("rutaDeLogin", () => {
  it("conserva la ruta y la consulta como destino", () => {
    expect(rutaDeLogin("/detalle/ALR-001?x=1")).toBe("/login?siguiente=%2Fdetalle%2FALR-001%3Fx%3D1");
  });

  it("no anida el login dentro del login", () => {
    expect(rutaDeLogin("/login?siguiente=%2Fpanel")).toBe("/login");
    expect(rutaDeLogin("/")).toBe("/login");
  });
});
