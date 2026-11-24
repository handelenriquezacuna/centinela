import type { Alerta, Pagina } from "./api/tipos";
import { aplicarAlerta, compararCola, ordenarCola } from "./cola";

function alerta(id: string, campos: Partial<Alerta> = {}): Alerta {
  return {
    _id: id,
    esquema_version: 2,
    transaccion_id: `TXN-${id}`,
    puntaje: 60,
    severidad: "ALTA",
    estado: "nueva",
    agente_id: null,
    fecha_creacion: "2026-11-23T10:00:00Z",
    cuenta_origen: "8712-4455",
    cuenta_destino: "6033-9001",
    monto_crc: 100_000,
    moneda: "CRC",
    cliente_nombre: "Cliente",
    reglas_disparadas: [],
    historial: [],
    ...campos,
  };
}

function pagina(...elementos: Alerta[]): Pagina<Alerta> {
  return { total: elementos.length, limite: 50, desplazamiento: 0, elementos };
}

describe("orden de la cola (igual que ORDEN_PANEL)", () => {
  it("puntaje descendente, luego fecha descendente, luego el identificador", () => {
    const lista = ordenarCola([
      alerta("ALR-003", { puntaje: 40 }),
      alerta("ALR-002", { puntaje: 75, fecha_creacion: "2026-11-23T08:00:00Z" }),
      alerta("ALR-005", { puntaje: 75, fecha_creacion: "2026-11-23T09:00:00Z" }),
      alerta("ALR-004", { puntaje: 75, fecha_creacion: "2026-11-23T09:00:00Z" }),
    ]);
    expect(lista.map((a) => a._id)).toEqual(["ALR-004", "ALR-005", "ALR-002", "ALR-003"]);
    expect(compararCola(alerta("ALR-001"), alerta("ALR-001"))).toBe(0);
  });
});

describe("aplicar una alerta del canal", () => {
  it("una alerta nueva entra en su lugar y suma al total", () => {
    const actual = pagina(alerta("ALR-001", { puntaje: 80 }), alerta("ALR-002", { puntaje: 50 }));
    const nueva = aplicarAlerta(actual, alerta("ALR-003", { puntaje: 65 }));
    expect(nueva.elementos.map((a) => a._id)).toEqual(["ALR-001", "ALR-003", "ALR-002"]);
    expect(nueva.total).toBe(3);
  });

  it("una alerta que ya estaba se reemplaza, sin duplicarse ni cambiar el total", () => {
    const actual = pagina(alerta("ALR-001", { puntaje: 80 }), alerta("ALR-002", { puntaje: 50 }));
    const nueva = aplicarAlerta(actual, alerta("ALR-002", { puntaje: 90, estado: "en_revision" }));
    expect(nueva.elementos.map((a) => [a._id, a.estado])).toEqual([
      ["ALR-002", "en_revision"],
      ["ALR-001", "nueva"],
    ]);
    expect(nueva.total).toBe(2);
  });
});
