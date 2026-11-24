import type { AlertaDeCaso, CasoDestino, SesionActual } from "../api/tipos";

export const SESION_AGENTE: SesionActual = {
  agente_id: "AGT-901",
  rol: "agente",
  expira: "2026-11-23T20:00:00Z",
  permisos: ["buscar", "detalle", "panel", "red_mulas", "resolver", "sesion"],
  navegacion: [
    { ruta: "/panel", rotulo: "Panel" },
    { ruta: "/busqueda", rotulo: "Búsqueda" },
  ],
  paginas: ["/", "/login", "/panel", "/detalle", "/detalle/{alerta_id}", "/busqueda", "/red-mulas", "/red-mulas/{cuenta}"],
};

export function alertaDeCaso(id: string, campos: Partial<AlertaDeCaso> = {}): AlertaDeCaso {
  return {
    _id: id,
    estado: "nueva",
    severidad: "CRITICA",
    puntaje: 75,
    cliente_nombre: "María Rodríguez Vargas",
    cuenta_origen: "8712-4455",
    cuenta_destino: "6033-9001",
    monto_crc: 750_000,
    fecha_creacion: "2026-10-15T04:47:00Z",
    agente_id: null,
    ...campos,
  };
}

export function casoDestino(cuenta: string, alertas: AlertaDeCaso[], campos: Partial<CasoDestino> = {}): CasoDestino {
  return {
    cuenta_destino: cuenta,
    total_alertas: alertas.length,
    victimas: alertas.length,
    monto_total_crc: alertas.reduce((suma, a) => suma + a.monto_crc, 0),
    severidad_max: "CRITICA",
    puntaje_max: 75,
    mas_antigua: "2026-10-15T04:47:00Z",
    mas_reciente: "2026-10-15T04:47:00Z",
    reglas: [{ codigo: "R-01", veces: alertas.length }],
    alertas,
    alertas_ocultas: 0,
    en_lista_riesgo: false,
    sugerencia: null,
    acciones: [{ destino: "en_revision", rotulo: "Tomar" }],
    ...campos,
  };
}
