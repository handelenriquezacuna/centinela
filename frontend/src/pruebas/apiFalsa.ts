/*
 * `fetch` de prueba: responde por metodo y ruta, y guarda cada pedido para afirmar sobre
 * el cuerpo. Una ruta sin respuesta declarada es un 404 en la forma unica del error.
 */
import { vi } from "vitest";

export interface Pedido {
  metodo: string;
  ruta: string;
  cuerpo: unknown;
}

type Respuesta = { estado?: number; cuerpo?: unknown } | ((pedido: Pedido) => { estado?: number; cuerpo?: unknown });

export function instalarApiFalsa(rutas: Record<string, Respuesta>) {
  const pedidos: Pedido[] = [];
  const espia = vi.fn(async (entrada: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(entrada), "http://localhost");
    const metodo = (init?.method ?? "GET").toUpperCase();
    const pedido: Pedido = {
      metodo,
      ruta: url.pathname + url.search,
      cuerpo: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    };
    pedidos.push(pedido);
    const declarada = rutas[`${metodo} ${url.pathname}`];
    const resuelta = typeof declarada === "function" ? declarada(pedido) : declarada;
    if (resuelta === undefined) {
      return new Response(
        JSON.stringify({ error: { codigo: "no_encontrado", mensaje: `sin doble para ${metodo} ${url.pathname}`, detalles: [] } }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      );
    }
    const estado = resuelta.estado ?? 200;
    return new Response(estado === 204 ? null : JSON.stringify(resuelta.cuerpo ?? null), {
      status: estado,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", espia);
  return { pedidos, espia };
}

export function errorApi(estado: number, codigo: string, mensaje: string) {
  return { estado, cuerpo: { error: { codigo, mensaje, detalles: [] } } };
}
