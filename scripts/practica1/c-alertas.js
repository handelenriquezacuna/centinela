// ============================================================
// PERSONA C — Alertas y trabajo del agente
// Colecciones: agentes, alertas, casos
// IDs a usar: ver CONTRATO-IDS.md (AGT-001..002, ALR-001, CAS-001)
// Referencias: transaccion_id usa TXN-001 (de B), reglas_disparadas usa
// REG-001..003 (de B) — ya fijadas en el contrato.
// ============================================================

// ------------------------------------------------------------
// 1. AGENTES — la más simple, hacerla primero
// ------------------------------------------------------------
// TODO: validator — campos: nombre, rol, activo.
db.createCollection("agentes", {
  validator: { $jsonSchema: { /* TODO */ } }
});

db.agentes.insertMany([
  // { _id: "AGT-001", nombre: "José Solís", rol: "agente", activo: true },
  // { _id: "AGT-002", nombre: "Laura Méndez", rol: "agente", activo: true },
]);

// TODO: índice sobre rol (para filtrar agentes vs supervisores más adelante)
// db.agentes.createIndex({ rol: 1 }, { name: "idx_agentes_rol" });


// ------------------------------------------------------------
// 2. ALERTAS — la colección más importante del proyecto.
//    Historial de auditoría EMBEBIDO (se lee siempre junto con la alerta).
//    agente_id es REFERENCIA (cambia de dueño con frecuencia).
// ------------------------------------------------------------
// TODO: validator — campos: transaccion_id, reglas_disparadas (array),
// puntaje, severidad, estado, agente_id, historial (array embebido de
// { accion, agente, ts, nota }).
db.createCollection("alertas", {
  validator: { $jsonSchema: { /* TODO */ } }
});

// Usar EXACTO el caso de uso UC-02/UC-03: puntaje 75, severidad CRITICA,
// las 3 reglas REG-001/002/003, resuelta por AGT-001 (José Solís) como
// "confirmada" con la nota "Cliente confirma llamada previa".
db.alertas.insertMany([
  // {
  //   _id: "ALR-001",
  //   transaccion_id: "TXN-001",
  //   reglas_disparadas: ["REG-001", "REG-002", "REG-003"],
  //   puntaje: 75,
  //   severidad: "CRITICA",
  //   estado: "confirmada",
  //   agente_id: "AGT-001",
  //   historial: [
  //     { accion: "confirmada", agente: "AGT-001", ts: new Date("2026-09-24T22:53:00-06:00"), nota: "Cliente confirma llamada previa" }
  //   ]
  // },
]);

// TODO: índice compuesto estado+severidad (para el panel) y uno sobre agente_id
// db.alertas.createIndex({ estado: 1, severidad: -1 }, { name: "idx_alertas_estado_severidad" });
// db.alertas.createIndex({ agente_id: 1 }, { name: "idx_alertas_agente" });


// ------------------------------------------------------------
// 3. CASOS — escalación de una alerta confirmada
// ------------------------------------------------------------
// TODO: validator — campos: alertas (array de IDs), estado.
db.createCollection("casos", {
  validator: { $jsonSchema: { /* TODO */ } }
});

db.casos.insertMany([
  // { _id: "CAS-001", alertas: ["ALR-001"], estado: "investigacion" },
]);

// TODO: índice sobre estado
// db.casos.createIndex({ estado: 1 }, { name: "idx_casos_estado" });


// ------------------------------------------------------------
// Consultas de comprobación de esta sección (agregar 2-3)
// ------------------------------------------------------------
// TODO, ejemplos:
// db.alertas.find({ severidad: "CRITICA" });
// db.alertas.findOne({ _id: "ALR-001" }).historial;
// db.casos.find({ estado: "investigacion" });

print("Persona C (alertas y trabajo): OK");
