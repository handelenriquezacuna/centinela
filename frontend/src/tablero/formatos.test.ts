import { numero, sentido, tiempoCorto, variacionTexto } from "./formatos";

describe("textos del tablero", () => {
  it("escribe el tiempo como el servidor", () => {
    expect(tiempoCorto(null)).toBe("-");
    expect(tiempoCorto(45)).toBe("45 s");
    expect(tiempoCorto(90)).toBe("1 min 30 s");
    expect(tiempoCorto(3700)).toBe("1 h 1 min");
  });

  it("usa coma decimal y colones con punto de miles", () => {
    expect(numero(3)).toBe("3");
    expect(numero(33.3)).toBe("33,3");
    expect(variacionTexto(2, 100)).toBe("+2 (100,0%)");
    expect(variacionTexto(-1800, null, " s")).toBe("-1800 s");
    expect(variacionTexto(1_500_000, 150, "", true)).toBe("+₡1.500.000 (150,0%)");
  });

  it("sin periodo anterior o sin delta no hay sentido", () => {
    const sin = { actual: 1, previo: null, delta: null, porcentaje: null };
    expect(sentido(sin, true)).toBe("sin_dato");
    expect(sentido({ actual: 2, previo: 1, delta: 1, porcentaje: 100 }, false)).toBe("sin_dato");
    expect(sentido({ actual: 2, previo: 1, delta: 1, porcentaje: 100 }, true)).toBe("sube");
    expect(sentido({ actual: 1, previo: 2, delta: -1, porcentaje: -50 }, true)).toBe("baja");
    expect(sentido({ actual: 1, previo: 1, delta: 0, porcentaje: 0 }, true)).toBe("igual");
  });
});
