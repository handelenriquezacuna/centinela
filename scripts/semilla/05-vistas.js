// ============================================================
// H-24 · Vistas de consulta guardadas
//
// Las consultas que el agente y el supervisor repiten viven en el servidor como vistas
// (`db.createView`), no dispersas por el codigo. Una vista es de solo lectura, se consulta
// como una coleccion (`find`, `aggregate`, `count`) y NO guarda datos: cada lectura
// ejecuta su pipeline sobre la coleccion de origen y aprovecha sus indices.
//
//   vista_alertas_abiertas    alertas por atender (nueva y en_revision) con cliente,
//                             cuentas y monto, las mas graves primero. Sirve la cola del
//                             panel con el indice idx_alertas_estado_puntaje_fecha.
//   vista_cuentas_en_riesgo   cuentas de DESTINO con su cantidad de alertas, el monto
//                             recibido y si ya estan en listas_riesgo. Responde "a que
//                             cuentas estamos mandando plata sospechosa".
//   vista_resumen_por_regla   cuantas alertas disparo cada regla, con su codigo y
//                             descripcion. Lee las dos formas de reglas_disparadas
//                             (v1: REG-001; v2: {regla_id, ...}), igual que la API.
//
// Va DESPUES de 01, 02 y 03: las vistas apuntan a colecciones que 01 crea, y 01 hace
// drop de ellas. Una vista queda colgada si su origen se borra, y vuelve a funcionar al
// recrearse el origen; por eso este script se corre siempre al final de la siembra.
//
// Idempotente: borra cada vista y la crea de nuevo. Nunca toca una coleccion.
//
//   mongosh <uri> --quiet --file scripts/semilla/05-vistas.js
//
// Permisos (04-usuarios.js, sin cambios): centinela_app_rol tiene `find` sobre todas las
// colecciones de la base, y centinela_analista tiene el rol `read`; ambos leen las vistas.
// ============================================================

const NOMBRE_BASE = typeof BASE_CENTINELA === "string" ? BASE_CENTINELA : "antifraude";
const antifraude = db.getSiblingDB(NOMBRE_BASE);

const VISTAS = [
  {
    nombre: "vista_alertas_abiertas",
    origen: "alertas",
    pipeline: [
      { $match: { estado: { $in: ["nueva", "en_revision"] } } },
      {
        $project: {
          _id: 1,
          estado: 1,
          severidad: 1,
          puntaje: 1,
          cliente_nombre: 1,
          cuenta_origen: 1,
          cuenta_destino: 1,
          monto_crc: 1,
          agente_id: 1,
          fecha_creacion: 1
        }
      },
      { $sort: { puntaje: -1, fecha_creacion: -1 } }
    ]
  },
  {
    nombre: "vista_cuentas_en_riesgo",
    origen: "alertas",
    pipeline: [
      {
        $group: {
          _id: "$cuenta_destino",
          cantidad_alertas: { $sum: 1 },
          monto_total_crc: { $sum: "$monto_crc" },
          puntaje_maximo: { $max: "$puntaje" },
          ultima_alerta: { $max: "$fecha_creacion" }
        }
      },
      {
        $lookup: {
          from: "listas_riesgo",
          localField: "_id",
          foreignField: "cuenta",
          as: "reportes"
        }
      },
      {
        $project: {
          _id: 0,
          cuenta: "$_id",
          cantidad_alertas: 1,
          monto_total_crc: 1,
          puntaje_maximo: 1,
          ultima_alerta: 1,
          en_lista_riesgo: { $gt: [{ $size: "$reportes" }, 0] }
        }
      },
      { $sort: { cantidad_alertas: -1, monto_total_crc: -1, cuenta: 1 } }
    ]
  },
  {
    nombre: "vista_resumen_por_regla",
    origen: "alertas",
    pipeline: [
      {
        $project: {
          puntaje: 1,
          monto_crc: 1,
          regla_ids: {
            $map: {
              input: "$reglas_disparadas",
              as: "r",
              in: { $cond: [{ $eq: [{ $type: "$$r" }, "string"] }, "$$r", "$$r.regla_id"] }
            }
          }
        }
      },
      { $unwind: "$regla_ids" },
      {
        $group: {
          _id: "$regla_ids",
          cantidad_alertas: { $sum: 1 },
          puntaje_promedio: { $avg: "$puntaje" },
          monto_total_crc: { $sum: "$monto_crc" }
        }
      },
      {
        $lookup: {
          from: "reglas_deteccion",
          localField: "_id",
          foreignField: "_id",
          as: "regla"
        }
      },
      {
        $project: {
          _id: 1,
          codigo: { $first: "$regla.codigo" },
          descripcion: { $first: "$regla.descripcion" },
          cantidad_alertas: 1,
          puntaje_promedio: { $round: ["$puntaje_promedio", 1] },
          monto_total_crc: 1
        }
      },
      { $sort: { cantidad_alertas: -1, _id: 1 } }
    ]
  }
];

for (const vista of VISTAS) {
  if (antifraude.getCollectionNames().indexOf(vista.nombre) !== -1) {
    antifraude.getCollection(vista.nombre).drop();
  }
  antifraude.createView(vista.nombre, vista.origen, vista.pipeline);
  print("Vista " + vista.nombre + " creada sobre " + vista.origen);
}
