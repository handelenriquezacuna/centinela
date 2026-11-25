// ============================================================
// Centinela - demo mongosh, paso 1: como se mueven los datos
// ============================================================
//
// Inserta UNA transferencia de riesgo y la sigue en cuatro lugares:
//   (a) transacciones: el documento tal como llego
//   (b) alertas: la alerta que creo el motor D1, con reglas y puntaje
//   (c) deteccion_estado: el documento de reanudacion de D1
//   (d) un segundo insert de alerta para la misma transaccion: E11000
//
// Requiere la app corriendo (D1 procesa el change stream de transacciones).
//
// Uso:
//   mongosh "$URI_DEMO" --file scripts/demo/01-flujo.js
//
// La transferencia sale de CTA-001 (Maria Rodriguez, 8712-4455) hacia la
// cuenta mula 6033-9001 (lista de riesgo RSK-001), a las 22:47 de hoy en
// Costa Rica. Esa hora cae fuera del horario habitual 07:00-19:00.

load("scripts/demo/00-ayudas.js");

print("== Paso 1 - insert ==");

// El documento que se va a insertar, antes de insertarlo.
const doc = transferencia({
  origen: "CTA-001",
  sinpe_origen: "8712-4455",
  destino: "6033-9001",
  monto: 280000,
  fecha: hoyCR(22, 47)
});
print("Documento a insertar:");
printjson(doc);

const insercion = db.transacciones.insertOne(doc);
const ID = doc._id;
print("insertOne acknowledged: " + insercion.acknowledged + ", _id: " + insercion.insertedId);

// D1 procesa el change stream con un pequeno retraso.
print("Esperando a D1 (3 segundos)...");
sleep(3000);

print("");
print("== (a) transacciones, por _id ==");
printjson(db.transacciones.findOne({ _id: ID }));

print("");
print("== (b) alertas, por transaccion_id ==");
const alerta = db.alertas.findOne(
  { transaccion_id: ID },
  { _id: 1, transaccion_id: 1, puntaje: 1, severidad: 1, estado: 1, reglas_disparadas: 1 }
);
if (alerta === null) {
  print("Sin alerta para " + ID + " todavia. Reintentar esta misma consulta en unos segundos.");
} else {
  print("alerta " + alerta._id + " | puntaje " + alerta.puntaje + " | severidad " +
    alerta.severidad + " | estado " + alerta.estado);
  alerta.reglas_disparadas.forEach(function (regla) {
    print("  " + regla.codigo + " (peso " + regla.peso + ", version " + regla.version + "): " +
      regla.explicacion);
  });
}

print("");
print("== (c) deteccion_estado, documento de reanudacion de D1 ==");
db.deteccion_estado.find(
  { clase: "reanudacion" },
  { _id: 1, clase: 1, ultima_transaccion_id: 1 }
).forEach(function (estado) {
  printjson(estado);
});

print("");
print("== (d) segunda alerta para la misma transaccion: idempotencia ==");
// Copia de la alerta con otro _id. Lo unico que choca es transaccion_id,
// que tiene indice unico: una transaccion produce una sola alerta.
const original = db.alertas.findOne({ transaccion_id: ID });
const duplicada = Object.assign({}, original, { _id: "ALR-9" + ID.slice(4) });
try {
  db.alertas.insertOne(duplicada);
  print("ERROR: la base acepto una segunda alerta para " + ID);
} catch (e) {
  print("codigo " + e.code + ": " + e.message);
}
print("alertas con transaccion_id " + ID + ": " +
  db.alertas.countDocuments({ transaccion_id: ID }));
