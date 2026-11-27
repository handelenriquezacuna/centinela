import type { Alerta, Pagina } from "./api/tipos";
import { aplicarAlerta, compararCola, esPrioritaria, ordenarCola } from "./cola";

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

describe("T-82: las alertas con 'No fui yo' del cliente van primero", () => {
  const noFui = { valor: "no_fui_yo" as const, fecha: "2026-11-23T10:05:00Z" };

  it("una abierta con 'No fui yo' pasa por encima de un puntaje mayor", () => {
    const lista = ordenarCola([
      alerta("ALR-001", { puntaje: 100 }),
      alerta("ALR-002", { puntaje: 35, respuesta_cliente: noFui }),
      alerta("ALR-003", { puntaje: 60, estado: "en_revision", respuesta_cliente: noFui }),
    ]);
    expect(lista.map((a) => a._id)).toEqual(["ALR-003", "ALR-002", "ALR-001"]);
  });

  it("'Si fui yo' no sube, y una alerta ya cerrada con 'No fui yo' tampoco", () => {
    expect(esPrioritaria(alerta("A", { respuesta_cliente: { ...noFui, valor: "si_fui_yo" } }))).toBe(false);
    expect(esPrioritaria(alerta("B", { estado: "confirmada", respuesta_cliente: noFui }))).toBe(false);
    expect(esPrioritaria(alerta("C", { respuesta_cliente: noFui }))).toBe(true);
  });

  it("la respuesta que llega por el canal reordena la cola sin pedir nada", () => {
    const antes = pagina(alerta("ALR-001", { puntaje: 90 }), alerta("ALR-002", { puntaje: 40 }));
    const despues = aplicarAlerta(antes, alerta("ALR-002", { puntaje: 40, respuesta_cliente: noFui }));
    expect(despues.elementos.map((a) => a._id)).toEqual(["ALR-002", "ALR-001"]);
    expect(despues.total).toBe(2);
  });
});
