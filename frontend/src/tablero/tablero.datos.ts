/* Respuesta fija de GET /api/v1/tablero para las pruebas: los mismos numeros siempre. */
import type { RespuestaTablero, VariacionConteo } from "../api/tipos";

function v(actual: number, previo: number): VariacionConteo {
  return { actual, previo, delta: actual - previo, porcentaje: previo ? Math.round(((actual - previo) * 1000) / previo) / 10 : null };
}

export const TABLERO_CON_DATOS: RespuestaTablero = {
  dias: 7,
  periodos: [1, 7, 14, 30],
  comando_recalculo: "python tareas.py indicadores",
  resumen: {
    vacio: false,
    dias: 7,
    periodo_actual: { desde: "2026-11-17", hasta: "2026-11-23" },
    periodo_previo: { desde: "2026-11-10", hasta: "2026-11-16" },
    calculado_en: "2026-11-23T18:30:00Z",
    actual: {
      total: 10,
      por_severidad: { CRITICA: 4, ALTA: 3, MEDIA: 3 },
      por_estado: { nueva: 3, en_revision: 1, confirmada: 4, descartada: 1, escalada: 1 },
      por_agente: { "AGT-901": 5 },
      por_hora: Object.fromEntries(Array.from({ length: 24 }, (_, h) => [String(h).padStart(2, "0"), h === 22 ? 6 : h === 9 ? 2 : 0])),
      dias_con_datos: 7,
      kpi: { disponible: true, tiempo_decision_s: 5400, monto_protegido_crc: 2_500_000, tasa_confirmacion: 80, pendientes: 4, pendiente_mas_antigua: "2026-11-20T12:00:00Z" },
    },
    previo: {
      total: 8,
      por_severidad: { CRITICA: 2, ALTA: 3, MEDIA: 3 },
      por_estado: { nueva: 1, en_revision: 1, confirmada: 3, descartada: 2, escalada: 1 },
      por_agente: null,
      por_hora: null,
      dias_con_datos: 7,
      kpi: { disponible: true, tiempo_decision_s: 7200, monto_protegido_crc: 1_000_000, tasa_confirmacion: 60, pendientes: 2, pendiente_mas_antigua: null },
    },
    total: v(10, 8),
    severidad: { CRITICA: v(4, 2), ALTA: v(3, 3), MEDIA: v(3, 3) },
    estado: { nueva: v(3, 1), en_revision: v(1, 1), confirmada: v(4, 3), descartada: v(1, 2), escalada: v(1, 1) },
    hay_periodo_previo: true,
    kpi: {
      disponible: true,
      tiempo_decision_s: { actual: 5400, previo: 7200, delta: -1800, porcentaje: -25 },
      monto_protegido_crc: { actual: 2_500_000, previo: 1_000_000, delta: 1_500_000, porcentaje: 150 },
      tasa_confirmacion: { actual: 80, previo: 60, delta: 20, porcentaje: 33.3 },
      pendientes: { actual: 4, previo: 2, delta: 2, porcentaje: 100 },
      pendiente_mas_antigua: "2026-11-20T12:00:00Z",
      antiguedad_horas: 78.5,
    },
  },
  graficos: {
    estado: {
      ancho: 430,
      alto: 130,
      etiqueta: 120,
      filas: [
        { y: 0, rotulo: "nueva", valor: 3, clase: "barra-nueva", ancho: 195 },
        { y: 26, rotulo: "en_revision", valor: 1, clase: "barra-en_revision", ancho: 65 },
        { y: 52, rotulo: "confirmada", valor: 4, clase: "barra-confirmada", ancho: 260 },
      ],
    },
    severidad: {
      ancho: 430,
      alto: 78,
      etiqueta: 120,
      filas: [
        { y: 0, rotulo: "CRITICA", valor: 4, clase: "barra-critica", ancho: 260 },
        { y: 26, rotulo: "ALTA", valor: 3, clase: "barra-alta", ancho: 195 },
        { y: 52, rotulo: "MEDIA", valor: 3, clase: "barra-media", ancho: 195 },
      ],
    },
    agente: { ancho: 430, alto: 26, etiqueta: 120, filas: [{ y: 0, rotulo: "AGT-901", valor: 5, clase: "barra-agente", ancho: 260 }] },
    hora: {
      ancho: 480,
      alto: 140,
      base: 120,
      paso: 20,
      columnas: [
        { x: 180, y: 80, alto: 40, hora: "09", valor: 2 },
        { x: 440, y: 0, alto: 120, hora: "22", valor: 6 },
        { x: 0, y: 120, alto: 0, hora: "00", valor: 0 },
      ],
    },
  },
};
