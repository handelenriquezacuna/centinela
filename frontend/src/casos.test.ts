import type { CasoDestino, Pagina } from "./api/tipos";
import { aplicarCaso, compararCasos } from "./casos";

function caso(cuenta: string, campos: Partial<CasoDestino> = {}): CasoDestino {
  return {
    cuenta_destino: cuenta,
    total_alertas: 1,
    victimas: 1,
    monto_total_crc: 100_000,
    severidad_max: "ALTA",
    puntaje_max: 60,
    mas_antigua: "2026-11-23T10:00:00Z",
    mas_reciente: "2026-11-23T10:00:00Z",
    reglas: [],
    alertas: [],
    alertas_ocultas: 0,
    en_lista_riesgo: false,
    sugerencia: null,
    acciones: [],
    ...campos,
  };
}

function pagina(...elementos: CasoDestino[]): Pagina<CasoDestino> {
  return { total: elementos.length, limite: 50, desplazamiento: 0, elementos };
}

describe("orden de las cuentas destino (igual que clave_de_orden en Python)", () => {
  it("severidad, luego puntaje, luego la mas antigua, luego la cuenta", () => {
    const lista = [
      caso("3000-0000", { severidad_max: "MEDIA", puntaje_max: 99 }),
      caso("2000-0000", { severidad_max: "CRITICA", puntaje_max: 70 }),
      caso("1000-0002", { mas_antigua: "2026-11-23T09:00:00Z" }),
      caso("1000-0001", { mas_antigua: "2026-11-23T09:00:00Z" }),
      caso("1000-0003", { puntaje_max: 65 }),
    ].sort(compararCasos);
    expect(lista.map((c) => c.cuenta_destino)).toEqual([
      "2000-0000",
      "1000-0003",
      "1000-0001",
      "1000-0002",
      "3000-0000",
    ]);
  });
});

describe("aplicarCaso", () => {
  it("una cuenta nueva entra en su lugar y suma al total", () => {
    const resultado = aplicarCaso(pagina(caso("1000-0001")), "2000-0000", caso("2000-0000", { severidad_max: "CRITICA" }));
    expect(resultado.elementos.map((c) => c.cuenta_destino)).toEqual(["2000-0000", "1000-0001"]);
    expect(resultado.total).toBe(2);
  });

  it("una cuenta que ya estaba se reemplaza sin duplicarse", () => {
    const resultado = aplicarCaso(pagina(caso("1000-0001")), "1000-0001", caso("1000-0001", { total_alertas: 4 }));
    expect(resultado.elementos).toHaveLength(1);
    expect(resultado.elementos[0]?.total_alertas).toBe(4);
    expect(resultado.total).toBe(1);
  });

  it("una cuenta sin alertas abiertas sale de la lista", () => {
    const resultado = aplicarCaso(pagina(caso("1000-0001"), caso("2000-0000")), "1000-0001", null);
    expect(resultado.elementos.map((c) => c.cuenta_destino)).toEqual(["2000-0000"]);
    expect(resultado.total).toBe(1);
  });
});
