/*
 * Datos de prueba de la red de mulas y el Sankey: el caso de la defensa (TXN-001..007).
 * Victima -> 6033-9001 (280.000) -> 7104-2288 (276.000) -> 8455-1177 (185.000) ->
 * 6001-3344 (90.000). Los anchos son los que dispone el servidor: la SPA no los calcula.
 */
import type { SesionActual } from "../api/tipos";
import type { DisposicionGrafo, DisposicionSankey, EntradaListaRiesgo, RedMulas, RespuestaGrafo } from "../api/tiposRed";
import { SESION_AGENTE } from "./datos";

export const SESION_SUPERVISOR: SesionActual = {
  ...SESION_AGENTE,
  agente_id: "AGT-902",
  rol: "supervisor",
  permisos: [...SESION_AGENTE.permisos, "gestionar_listas"],
};

const CUENTAS = ["6033-9001", "7104-2288", "8455-1177", "6001-3344"] as const;
const MONTOS = [280_000, 276_000, 185_000, 90_000] as const;
const GROSORES = [140, 138, 92, 45] as const;
const FECHAS = ["2026-09-25T04:00:00Z", "2026-09-25T04:20:00Z", "2026-09-25T04:40:00Z", "2026-09-25T05:10:00Z"] as const;

export function disposicionDelCaso(enLista: readonly string[] = ["6033-9001"]): DisposicionSankey {
  const origenes = ["CLI-001", ...CUENTAS.slice(0, 3)];
  return {
    ancho: 1200,
    alto: 260,
    columnas: 5,
    saltos: 3,
    transferencias: 4,
    resumen: "Recorrido del dinero desde la víctima María Rodríguez Vargas: 4 transferencias, de ₡280.000 a ₡90.000 en la última",
    nodos: [
      { x: 24, y: 60, ancho: 16, alto: 140, cuenta: "8712-4455", columna: 0, externa: false, en_lista: false, inicio: false, victima: true, nombre: "María Rodríguez Vargas" },
      ...CUENTAS.map((cuenta, i) => ({
        x: 254 + 230 * i,
        y: 60,
        ancho: 16,
        alto: GROSORES[i] ?? 20,
        cuenta,
        columna: i + 1,
        externa: false,
        en_lista: enLista.includes(cuenta),
        inicio: i === 0,
        victima: false,
        nombre: null,
      })),
    ],
    bandas: MONTOS.map((monto, i) => ({
      d: `M ${40 + 230 * i} 130 L ${254 + 230 * i} 130`,
      grosor: GROSORES[i] ?? 2,
      tx: 150 + 230 * i,
      ty: 124,
      salto: i,
      origen: origenes[i] ?? "",
      destino: CUENTAS[i] ?? "",
      monto_crc: monto,
      fecha: FECHAS[i] ?? "",
      transaccion_id: `TXN-00${i + 1}`,
    })),
    cuentas: [
      { victima: true, nombre: "María Rodríguez Vargas", cuenta: "8712-4455", cuenta_id: "CTA-001", externa: false, en_lista: false, columna: 0 },
      ...CUENTAS.map((cuenta, i) => ({
        victima: false,
        nombre: null,
        cuenta,
        cuenta_id: null,
        externa: false,
        en_lista: enLista.includes(cuenta),
        columna: i + 1,
      })),
    ],
  };
}

export function respuestaSankey(enLista: readonly string[] = ["6033-9001"]) {
  return { cuenta: "6033-9001", sankey: disposicionDelCaso(enLista), motivo: null };
}

export function redDelCaso(propuestas: string[] = ["7104-2288", "8455-1177", "6001-3344"]): RedMulas {
  return {
    cuenta: "6033-9001",
    desde: "2026-09-25T04:00:00Z",
    ventana_minutos: 60,
    saltos_maximos: 6,
    saltos_recorridos: 3,
    truncada: false,
    nodos: CUENTAS.map((cuenta, i) => ({ cuenta, cuenta_id: null, externa: false, nivel: i, en_lista_riesgo: cuenta === "6033-9001" })),
    aristas: [1, 2, 3].map((i) => ({
      transaccion_id: `TXN-00${i + 4}`,
      origen: CUENTAS[i - 1] ?? "",
      destino: CUENTAS[i] ?? "",
      monto_crc: MONTOS[i] ?? 0,
      fecha: FECHAS[i] ?? "",
      salto: i,
      minutos_desde_anterior: i === 1 ? null : 20,
    })),
    propuestas_lista_riesgo: propuestas,
  };
}

export const GRAFO_DEL_CASO: DisposicionGrafo = {
  ancho: 900,
  alto: 200,
  nodos: CUENTAS.map((cuenta, i) => ({
    x: 20 + 220 * i,
    y: 60,
    ancho: 160,
    alto: 48,
    cuenta,
    cuenta_id: null,
    externa: false,
    en_lista: cuenta === "6033-9001",
    inicio: i === 0,
  })),
  aristas: [1, 2, 3].map((i) => ({
    x1: 180 + 220 * (i - 1),
    y1: 84,
    x2: 20 + 220 * i,
    y2: 84,
    tx: 100 + 220 * i,
    ty: 76,
    monto_crc: MONTOS[i] ?? 0,
    minutos: i === 1 ? null : 20,
    salto: i,
  })),
};

export const ENTRADA_6033: EntradaListaRiesgo = {
  _id: "RSK-001",
  cuenta: "6033-9001",
  motivo: "Reportada por tres víctimas",
  fecha_reporte: "2026-09-24T16:00:00Z",
  vigente: true,
};

export function respuestaGrafo(opciones: { propuestas?: string[]; entradas?: EntradaListaRiesgo[] } = {}): { cuerpo: RespuestaGrafo } {
  return {
    cuerpo: {
      red: redDelCaso(opciones.propuestas),
      grafo: GRAFO_DEL_CASO,
      entradas_lista: opciones.entradas ?? [ENTRADA_6033],
    },
  };
}
