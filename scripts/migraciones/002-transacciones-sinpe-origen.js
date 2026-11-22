// ============================================================
// Centinela - migracion 002: transacciones.sinpe_origen (H-23)
// ============================================================
//
//   python tareas.py migrar
//   mongosh "<uri>" --file scripts/migraciones/002-transacciones-sinpe-origen.js
//
// Base: "antifraude". Para otra base (las pruebas), definir BASE_MIGRACION antes:
//   mongosh "<uri>" --eval 'var BASE_MIGRACION="antifraude_pruebas"' --file <este>
//
// Que cambia: cada transaccion lleva sinpe_origen, el numero SINPE de su cuenta
// de origen (cuentas.cuenta_sinpe). Es el mismo espacio de valores que
// cuenta_destino, y es lo que permite que $graphLookup una un eslabon de la
// cadena de mulas con el siguiente (connectFromField cuenta_destino,
// connectToField sinpe_origen).
//
// Reejecutable e idempotente: solo toca documentos SIN sinpe_origen, y lo hace
// con un pipeline de agregacion que termina en $merge (whenMatched: merge) sobre
// la misma coleccion. Una segunda corrida encuentra cero pendientes y no cambia
// nada. No cambia esquema_version: el campo es aditivo y opcional en el
// validador, y el sistema lee igual los documentos con y sin el campo.
//
// Una transaccion cuya cuenta_origen no existe en cuentas no se puede rellenar:
// queda sin el campo y se lista. No se inventa un numero.
// ============================================================

const NOMBRE_BASE = typeof BASE_MIGRACION !== "undefined" ? BASE_MIGRACION : "antifraude";
const base = db.getSiblingDB(NOMBRE_BASE);

const PENDIENTE = { sinpe_origen: { $exists: false } };
const yaTenian = base.transacciones.countDocuments({ sinpe_origen: { $exists: true } });
const pendientes = base.transacciones.countDocuments(PENDIENTE);

if (pendientes > 0) {
  base.transacciones.aggregate([
    { $match: PENDIENTE },
    {
      $lookup: {
        from: "cuentas",
        localField: "cuenta_origen",
        foreignField: "_id",
        pipeline: [{ $project: { _id: 0, cuenta_sinpe: 1 } }],
        as: "origen"
      }
    },
    { $match: { "origen.0": { $exists: true } } },
    { $project: { _id: 1, sinpe_origen: { $first: "$origen.cuenta_sinpe" } } },
    {
      $merge: {
        into: "transacciones",
        on: "_id",
        whenMatched: "merge",
        whenNotMatched: "discard"
      }
    }
  ]);
}

const sinCuenta = base.transacciones.find(PENDIENTE, { _id: 1 }).toArray().map((t) => t._id);

print("migracion 002 transacciones.sinpe_origen sobre " + NOMBRE_BASE);
print("  rellenadas: " + (pendientes - sinCuenta.length));
print("  ya lo tenian: " + yaTenian);
print("  sin cuenta de origen, siguen sin el campo: " + sinCuenta.length +
      (sinCuenta.length ? " (" + sinCuenta.slice(0, 20).join(", ") + ")" : ""));
