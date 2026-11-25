// ============================================================
// Centinela - demo mongosh: limpieza
// ============================================================
//
// Borra SOLO lo que insertaron 01-flujo.js y 02-reglas.js, y las alertas
// (y notificaciones) que D1 y D2 crearon para esas transacciones.
//
// Como se identifica lo de la demo: el validador de `transacciones` no tiene
// un campo libre para marcarla, asi que se usan dos criterios juntos:
//   - _id de 6 digitos (TXN-NNNNNN): los ids de la semilla son de 3 digitos
//   - cuenta_origen CTA-001: la victima de la demo. El generador usa cuentas
//     CTA-NNNN de 4 digitos, asi que una carga masiva no se toca.
// No depende del dia en que se corrio la demo.
//
// Es repetible: si no hay nada que borrar, imprime ceros.
//
// Uso:
//   mongosh "$URI_DEMO" --file scripts/demo/99-limpiar.js

load("scripts/demo/00-ayudas.js");

print("== Transacciones de la demo a borrar ==");
const transacciones = db.transacciones.find(
  {
    _id: { $regex: /^TXN-\d{6}$/ },
    cuenta_origen: "CTA-001"
  },
  { _id: 1, cuenta_destino: 1, monto_crc: 1, fecha: 1 }
).sort({ _id: 1 }).toArray();
transacciones.forEach(function (t) {
  print("  " + t._id + "  " + t.cuenta_destino + "  " + t.monto_crc + " CRC  " + t.fecha.toISOString());
});
const idsTransacciones = transacciones.map(function (t) { return t._id; });

print("");
print("== Alertas de esas transacciones ==");
const idsAlertas = db.alertas.find(
  { transaccion_id: { $in: idsTransacciones } },
  { _id: 1 }
).toArray().map(function (a) { return a._id; });
print("  " + (idsAlertas.join(" ") || "ninguna"));

const borradasNotificaciones = db.notificaciones.deleteMany({ alerta_id: { $in: idsAlertas } });
const borradasAlertas = db.alertas.deleteMany({ _id: { $in: idsAlertas } });
const borradasTransacciones = db.transacciones.deleteMany({ _id: { $in: idsTransacciones } });

print("");
print("== Resultado ==");
print("  notificaciones borradas: " + borradasNotificaciones.deletedCount);
print("  alertas borradas:        " + borradasAlertas.deletedCount);
print("  transacciones borradas:  " + borradasTransacciones.deletedCount);
print("  transacciones de la semilla intactas (TXN-001..TXN-007): " +
  db.transacciones.countDocuments({ _id: { $in: ["TXN-001", "TXN-002", "TXN-003", "TXN-004", "TXN-005", "TXN-006", "TXN-007"] } }) + " de 7");
