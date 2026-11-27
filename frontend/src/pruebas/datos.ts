import type { AlertaDeCaso, CasoDestino, SesionActual } from "../api/tipos";

export const SESION_AGENTE: SesionActual = {
  agente_id: "AGT-901",
  nombre: "Agente E2E",
  rol: "agente",
  inicio: "/panel",
  expira: "2099-01-01T00:00:00Z",
  permisos: ["buscar", "detalle", "panel", "red_mulas", "resolver", "sesion"],
  navegacion: [
    { ruta: "/panel", rotulo: "Panel", area: "Alertas" },
    { ruta: "/busqueda", rotulo: "Búsqueda", area: "Alertas" },
    { ruta: "/red-mulas", rotulo: "Red de mulas", area: "Alertas" },
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

export const SESION_SUPERVISOR: SesionActual = {
  ...SESION_AGENTE,
  agente_id: "AGT-902",
  nombre: "Supervisor E2E",
  rol: "supervisor",
  inicio: "/tablero",
  permisos: [...SESION_AGENTE.permisos, "tablero", "ver_reglas", "editar_reglas"],
  navegacion: [
    ...SESION_AGENTE.navegacion,
    { ruta: "/tablero", rotulo: "Tablero", area: "Supervisión" },
    { ruta: "/reglas", rotulo: "Reglas y política", area: "Supervisión" },
  ],
  paginas: [...SESION_AGENTE.paginas, "/tablero", "/reglas"],
};

export const SESION_ADMINISTRADOR: SesionActual = {
  ...SESION_SUPERVISOR,
  agente_id: "AGT-900",
  nombre: "Administrador E2E",
  rol: "administrador",
  inicio: "/administracion/salud",
  permisos: [...SESION_SUPERVISOR.permisos, "administracion", "gestionar_listas"],
  navegacion: [
    ...SESION_SUPERVISOR.navegacion,
    { ruta: "/administracion/salud", rotulo: "Salud del sistema", area: "Administración" },
    { ruta: "/administracion/respaldos", rotulo: "Respaldos", area: "Administración" },
    { ruta: "/administracion/usuarios", rotulo: "Usuarios del portal", area: "Administración" },
  ],
  paginas: [
    ...SESION_SUPERVISOR.paginas,
    "/administracion",
    "/administracion/salud",
    "/administracion/respaldos",
    "/administracion/usuarios",
  ],
};
