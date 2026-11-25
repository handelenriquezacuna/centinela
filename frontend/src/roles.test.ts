import { quienPuedeAbrir } from "./roles";

describe("quienPuedeAbrir", () => {
  it("dice el rol de las pantallas restringidas", () => {
    expect(quienPuedeAbrir("/tablero")).toBe("el supervisor y el administrador");
    expect(quienPuedeAbrir("/reglas")).toBe("el supervisor y el administrador");
    expect(quienPuedeAbrir("/administracion/respaldos")).toBe("el administrador");
  });

  it("no inventa nada para una pantalla que no conoce", () => {
    expect(quienPuedeAbrir("/panel")).toBeNull();
    expect(quienPuedeAbrir("/reglas-viejas")).toBeNull();
  });
});
