// ============================================================
// PERSONA A — Identidad y comportamiento
// Colecciones: clientes, cuentas, perfiles_comportamiento
// IDs a usar: ver CONTRATO-IDS.md (CLI-001..004, CTA-001..004)
// ============================================================
//
// No necesitas el archivo de B/C/D para escribir el tuyo: los IDs que ellos
// van a referenciar (CLI-001, CTA-001...) ya están fijados en el contrato.
//
// Qué hacer en cada bloque (mismo patrón que HospitalDB.js):
//   1. db.createCollection(nombre, { validator: { $jsonSchema: {...} } })
//   2. db.<coleccion>.insertMany([...documentos de ejemplo...])
//   3. db.<coleccion>.createIndex({...}, { name: "idx_..." })
//
// ------------------------------------------------------------
// 1. CLIENTES
// ------------------------------------------------------------
// TODO: validator $jsonSchema — campos sugeridos: nombre, cedula,
// fecha_registro. Usar bsonType, required, y description por campo.
db.createCollection("clientes", {
  validator: {
    $jsonSchema: {
      // TODO: completar bsonType "object", required: [...], properties: {...}
    }
  }
});

// TODO: insertMany con los 4 clientes del CONTRATO-IDS.md (CLI-001..004)
db.clientes.insertMany([
  // { _id: "CLI-001", nombre: "María Rodríguez Vargas", cedula: "1-1111-1111", fecha_registro: new Date("2025-01-15") },
  // ...
]);

// TODO: índice único sobre cedula
// db.clientes.createIndex({ cedula: 1 }, { name: "idx_clientes_cedula", unique: true });


// ------------------------------------------------------------
// 2. CUENTAS
// ------------------------------------------------------------
// TODO: validator — campos: cuenta_sinpe, cliente_id (referencia a CLI-xxx,
// NO embebido — justificar en el documento por qué referencia y no embebido:
// una cuenta puede cambiar de titular, el cliente no debe cargar sus cuentas).
db.createCollection("cuentas", {
  validator: { $jsonSchema: { /* TODO */ } }
});

// TODO: insertMany con las 4 cuentas (CTA-001..004), usando cuenta_sinpe
// EXACTO del contrato: 8712-4455 para CTA-001 (María).
db.cuentas.insertMany([
  // { _id: "CTA-001", cuenta_sinpe: "8712-4455", cliente_id: "CLI-001" },
]);

// TODO: índice compuesto cliente_id + índice único sobre cuenta_sinpe
// db.cuentas.createIndex({ cliente_id: 1 }, { name: "idx_cuentas_cliente" });
// db.cuentas.createIndex({ cuenta_sinpe: 1 }, { name: "idx_cuentas_sinpe", unique: true });


// ------------------------------------------------------------
// 3. PERFILES_COMPORTAMIENTO (colección materializada — para esta práctica
//    se inserta a mano; en el proyecto real la produce un pipeline $merge)
// ------------------------------------------------------------
// TODO: validator — campos: cliente_id, promedio, desviacion, maximo,
// num_transferencias, horario_habitual {inicio, fin}, destinos_frecuentes [].
db.createCollection("perfiles_comportamiento", {
  validator: { $jsonSchema: { /* TODO */ } }
});

// Usar los valores YA documentados para CLI-001 (no inventar otros):
// 47 transferencias, promedio 45200, maximo 180000, horario 07:00-19:00.
db.perfiles_comportamiento.insertMany([
  // {
  //   cliente_id: "CLI-001",
  //   promedio: 45200,
  //   maximo: 180000,
  //   num_transferencias: 47,
  //   horario_habitual: { inicio: "07:00", fin: "19:00" },
  //   destinos_frecuentes: [ /* TODO: 3-5 numeros de cuenta */ ]
  // },
]);

// TODO: índice único sobre cliente_id (1 perfil por cliente)
// db.perfiles_comportamiento.createIndex({ cliente_id: 1 }, { name: "idx_perfiles_cliente", unique: true });


// ------------------------------------------------------------
// Consultas de comprobación de esta sección (agregar 2-3)
// ------------------------------------------------------------
// TODO, ejemplos:
// db.clientes.find({ cedula: "1-1111-1111" });
// db.cuentas.find({ cliente_id: "CLI-001" });
// db.perfiles_comportamiento.findOne({ cliente_id: "CLI-001" });

print("Persona A (identidad y comportamiento): OK");
