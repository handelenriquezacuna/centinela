import type { AccionDisponible, DetalleAlerta } from "../api/tiposDetalle";

const TOMAR: AccionDisponible = { destino: "en_revision", rotulo: "Tomar", ayuda: "Te asigna la alerta y la pasa a En revisión.", cierra: false };
const CONFIRMAR: AccionDisponible = { destino: "confirmada", rotulo: "Confirmar fraude", ayuda: "Cierra la alerta como fraude real. No se puede deshacer.", cierra: true };
const DESCARTAR: AccionDisponible = { destino: "descartada", rotulo: "Descartar", ayuda: "Cierra la alerta como falso positivo. No se puede deshacer.", cierra: true };

export const ACCIONES_NUEVA = [TOMAR, DESCARTAR];
export const ACCIONES_EN_REVISION = [CONFIRMAR, DESCARTAR];

export function detalleDeAlerta(campos: Partial<DetalleAlerta> = {}, estado: "nueva" | "en_revision" | "confirmada" = "nueva"): DetalleAlerta {
  return {
    alerta: {
      _id: "ALR-001",
      esquema_version: 2,
      transaccion_id: "TXN-001",
      puntaje: 75,
      severidad: "CRITICA",
      estado,
      agente_id: estado === "nueva" ? null : "AGT-901",
      fecha_creacion: "2026-09-25T04:00:30Z",
      cuenta_origen: "8712-4455",
      cuenta_destino: "6033-9001",
      monto_crc: 280_000,
      moneda: "CRC",
      cliente_nombre: "María Rodríguez Vargas",
      reglas_disparadas: [],
      historial: [{ fecha: "2026-09-25T04:00:30Z", agente_id: null, accion: "nueva", nota: "" }],
    },
    es_v2: true,
    aviso_v1: null,
    transaccion: {
      transaccion_id: "TXN-001",
      cuenta_origen: "CTA-001",
      sinpe_origen: "8712-4455",
      cuenta_destino: "6033-9001",
      monto_crc: 280_000,
      moneda: "CRC",
      canal: "sinpe_movil",
      fecha: "2026-09-25T04:00:00Z",
    },
    cliente: { cliente_id: "CLI-001", nombre: "María Rodríguez Vargas", cedula: "1-0234-0567" },
    perfil: {
      promedio_crc: 40_000,
      maximo_crc: 90_000,
      num_transferencias: 24,
      horario_habitual: { inicio: "07:00", fin: "20:00" },
      destinos_frecuentes: ["8801-1234", "8802-5678"],
    },
    politica: { politica_id: "POL-001", version: 1, origen: "usada" },
    puntaje: {
      filas: [
        { regla_id: "REG-001", codigo: "R-01", version: 2, peso: 35, explicacion: "Monto 7 veces el promedio del cliente", evidencia: "₡280.000 contra un promedio de ₡40.000", acumulado: 35, ancho: 35 },
        { regla_id: "REG-007", codigo: "R-07", version: 1, peso: 40, explicacion: "Cuenta destino en lista de riesgo", evidencia: null, acumulado: 75, ancho: 40 },
      ],
      suma: 75,
      tope: false,
      maximo: 100,
      cortes: [
        { severidad: "MEDIA", minimo: 30, maximo: 49, inicio: 30, alcanzado: false },
        { severidad: "ALTA", minimo: 50, maximo: 69, inicio: 50, alcanzado: false },
        { severidad: "CRITICA", minimo: 70, maximo: 100, inicio: 70, alcanzado: true },
      ],
      ancho_puntaje: 75,
      umbral: 30,
    },
    acciones: estado === "nueva" ? ACCIONES_NUEVA : estado === "en_revision" ? ACCIONES_EN_REVISION : [],
    caso_id: null,
    nombres_agentes: { "AGT-901": "Agente E2E", "AGT-902": "Supervisor E2E" },
    ...campos,
  };
}
