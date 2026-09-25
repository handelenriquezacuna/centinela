// ============================================================
// PERSONA D — Cierre, métricas y ENSAMBLAJE del script final
// Colecciones propias: notificaciones, indicadores_diarios
// IDs a usar: ver CONTRATO-IDS.md (NOT-001)
// Referencias: alerta_id usa ALR-001 (de C) — ya fijada en el contrato.
//
// Además del bloque de colecciones de abajo, D tiene el rol de INTEGRADOR:
// cuando A, B y C terminen, ver el bloque "ENSAMBLAJE" al final de este
// archivo para armar el script único que se entrega.
// ============================================================

// ------------------------------------------------------------
// 1. NOTIFICACIONES
// ------------------------------------------------------------
// TODO: validator — campos: alerta_id, canal, estado_envio, fecha.
db.createCollection("notificaciones", {
  validator: { $jsonSchema: { /* TODO */ } }
});

db.notificaciones.insertMany([
  // { _id: "NOT-001", alerta_id: "ALR-001", canal: "correo", estado_envio: "enviado", fecha: new Date("2026-09-24T22:47:30-06:00") },
]);

// TODO: índice sobre alerta_id
// db.notificaciones.createIndex({ alerta_id: 1 }, { name: "idx_notif_alerta" });


// ------------------------------------------------------------
// 2. INDICADORES_DIARIOS (colección materializada — a mano en esta práctica)
// ------------------------------------------------------------
// TODO: validator — campos: fecha, total_alertas, por_severidad, por_estado.
db.createCollection("indicadores_diarios", {
  validator: { $jsonSchema: { /* TODO */ } }
});

db.indicadores_diarios.insertMany([
  // {
  //   fecha: "2026-09-24",
  //   total_alertas: 1,
  //   por_severidad: { CRITICA: 1, ALTA: 0, MEDIA: 0 },
  //   por_estado: { confirmada: 1, en_revision: 0, descartada: 0 }
  // },
]);

// TODO: índice único sobre fecha
// db.indicadores_diarios.createIndex({ fecha: 1 }, { name: "idx_indicadores_fecha", unique: true });


// ------------------------------------------------------------
// Consultas de comprobación de esta sección (agregar 2-3)
// ------------------------------------------------------------
// TODO, ejemplos:
// db.notificaciones.find({ alerta_id: "ALR-001" });
// db.indicadores_diarios.findOne({ fecha: "2026-09-24" });

print("Persona D (cierre y métricas): OK");


// ==============================================================
// ENSAMBLAJE — hacer esto AL FINAL, cuando A, B y C ya terminaron
// ==============================================================
// 1. Crear scripts/practica1/centinela-practica1.js con esta cabecera:
//
//    print("==================================================");
//    print(" INICIO DEL SCRIPT - Centinela Practica 1");
//    print("==================================================");
//    db = db.getSiblingDB("centinela");
//    db.dropDatabase();
//
// 2. Pegar, EN ORDEN, el contenido de a-identidad.js, b-transaccional.js,
//    c-alertas.js y d-cierre.js (sin los comentarios TODO ya resueltos).
// 3. Agregar al final un bloque de verificación general, estilo
//    README_HospitalDB.md sección 10:
//
//    print("clientes: " + db.clientes.countDocuments({}));
//    print("cuentas: " + db.cuentas.countDocuments({}));
//    print("transacciones: " + db.transacciones.countDocuments({}));
//    print("alertas: " + db.alertas.countDocuments({}));
//    print("reglas_deteccion: " + db.reglas_deteccion.countDocuments({}));
//    print("agentes: " + db.agentes.countDocuments({}));
//    print("casos: " + db.casos.countDocuments({}));
//    print("listas_riesgo: " + db.listas_riesgo.countDocuments({}));
//    print("perfiles_comportamiento: " + db.perfiles_comportamiento.countDocuments({}));
//    print("indicadores_diarios: " + db.indicadores_diarios.countDocuments({}));
//    print("notificaciones: " + db.notificaciones.countDocuments({}));
//    print("==================================================");
//    print(" SCRIPT FINALIZADO CORRECTAMENTE");
//    print("==================================================");
//
// 4. Probar de verdad, dos veces seguidas (debe dar el mismo resultado las
//    dos veces porque dropDatabase() recrea todo):
//
//      mongosh --file scripts/practica1/centinela-practica1.js
//      mongosh --file scripts/practica1/centinela-practica1.js
//
// 5. Confirmar en Compass: base "centinela", 11 colecciones, cada una con
//    sus documentos e índices (Collections > <nombre> > Indexes).
