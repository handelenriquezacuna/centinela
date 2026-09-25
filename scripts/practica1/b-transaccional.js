// ============================================================
// PERSONA B — Flujo transaccional y reglas
// Colecciones: transacciones, reglas_deteccion, listas_riesgo
// IDs a usar: ver CONTRATO-IDS.md (TXN-001.., REG-001..003, RSK-001..004)
// Referencias a otras piezas: cuenta_origen usa CTA-001..004 (de Persona A) —
// ya están fijadas en el contrato, no hace falta que su archivo exista.
// ============================================================

// ------------------------------------------------------------
// 1. REGLAS_DETECCION — hacerla primero, es la más simple y no depende de nada
// ------------------------------------------------------------
// TODO: validator — campos: codigo, descripcion, tipo, umbral, peso, activa.
db.createCollection("reglas_deteccion", {
  validator: { $jsonSchema: { /* TODO */ } }
});

// Usar EXACTO los pesos ya documentados: R-01=35, R-03=25, R-05=15.
db.reglas_deteccion.insertMany([
  // { _id: "REG-001", codigo: "R-01", descripcion: "Monto sobre 10x el promedio", peso: 35, activa: true },
  // { _id: "REG-002", codigo: "R-03", descripcion: "Destino nunca visto", peso: 25, activa: true },
  // { _id: "REG-003", codigo: "R-05", descripcion: "Fuera del horario habitual", peso: 15, activa: true },
]);

// TODO: índice sobre codigo (para buscar por "R-01" directo)
// db.reglas_deteccion.createIndex({ codigo: 1 }, { name: "idx_reglas_codigo", unique: true });


// ------------------------------------------------------------
// 2. LISTAS_RIESGO — la cadena de mulas del caso de uso UC-04
// ------------------------------------------------------------
// TODO: validator — campos: cuenta, motivo, fecha_reporte, vigente.
db.createCollection("listas_riesgo", {
  validator: { $jsonSchema: { /* TODO */ } }
});

db.listas_riesgo.insertMany([
  // { _id: "RSK-001", cuenta: "6033-9001", motivo: "Primer salto de fragmentacion", vigente: true },
  // { _id: "RSK-002", cuenta: "7104-2288", motivo: "Segundo salto", vigente: true },
  // { _id: "RSK-003", cuenta: "8455-1177", motivo: "Tercer salto", vigente: true },
  // { _id: "RSK-004", cuenta: "6001-3344", motivo: "Cuarto salto", vigente: true },
]);

// TODO: índice único sobre cuenta
// db.listas_riesgo.createIndex({ cuenta: 1 }, { name: "idx_riesgo_cuenta", unique: true });


// ------------------------------------------------------------
// 3. TRANSACCIONES — la más importante: aquí va la transacción del caso de uso
// ------------------------------------------------------------
// TODO: validator — campos: cuenta_origen, cuenta_destino, monto, canal, fecha.
db.createCollection("transacciones", {
  validator: { $jsonSchema: { /* TODO */ } }
});

// La sospechosa (TXN-001) usa los valores EXACTOS del caso de uso: CTA-001
// (María), destino 6033-9001, monto 750000, 22:47. Agregar 2-3 normales más
// (CTA-002/003/004, montos chicos, horario de oficina) para que se note el contraste.
db.transacciones.insertMany([
  // { _id: "TXN-001", cuenta_origen: "CTA-001", cuenta_destino: "6033-9001", monto: 750000, canal: "SINPE", fecha: new Date("2026-09-24T22:47:00-06:00") },
  // { _id: "TXN-002", cuenta_origen: "CTA-002", cuenta_destino: "...", monto: 25000, canal: "SINPE", fecha: new Date("2026-09-24T10:15:00-06:00") },
]);

// TODO: índice compuesto cuenta_origen+fecha (para el flujo del panel) y otro
// para búsqueda por cuenta_destino.
// db.transacciones.createIndex({ cuenta_origen: 1, fecha: -1 }, { name: "idx_txn_cuenta_fecha" });
// db.transacciones.createIndex({ cuenta_destino: 1 }, { name: "idx_txn_destino" });


// ------------------------------------------------------------
// Consultas de comprobación de esta sección (agregar 2-3)
// ------------------------------------------------------------
// TODO, ejemplos:
// db.reglas_deteccion.find({ activa: true });
// db.transacciones.find({ monto: { $gt: 500000 } });
// db.listas_riesgo.find({ cuenta: "6033-9001" });

print("Persona B (transaccional y reglas): OK");
