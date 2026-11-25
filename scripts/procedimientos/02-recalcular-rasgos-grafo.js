// ============================================================
// T-76 / T-63 · Procedimiento: recalcular los rasgos de grafo de R-11
//
// Que hace: para cada cuenta destino con movimiento en el horizonte calcula, sobre
// `transacciones`, cuanto recibio, cuanto reenvio dentro de la ventana, a cuantos
// destinos, de cuantos origenes y cuantos saltos sigue el dinero. Es EL MISMO pipeline
// de app/repos/rasgos_grafo.py (funcion construir_pipeline): $match, $group,
// $graphLookup (sinpe_origen -> cuenta_destino, maxDepth 5), $addFields, $project y
// $merge. Despues escribe el documento de corrida y borra los rasgos de corridas
// anteriores, en ese orden. La definicion de cada numero esta en
// app/deteccion/rasgos_grafo.py.
//
// Entradas (variables globales opcionales, se pasan con --eval):
//   BASE_CENTINELA     base a usar. Por omision "antifraude".
//   CORTE              fin del rango, ISO 8601 en UTC. Por omision, ahora.
//   HORAS              horizonte hacia atras desde CORTE. Por omision 24 (config/centinela.yml).
//   VENTANA_MINUTOS    ventana del reenvio. Por omision, la de la regla red_mulas del catalogo.
//   VIGENCIA_SEGUNDOS  cuanto vale un rasgo desde su corte. Por omision 60 (config/centinela.yml).
//
// Escribe: deteccion_estado, documentos de clase `rasgo_grafo` (_id d3_rasgos_grafo:<cuenta>)
// y el de clase `rasgo_grafo_corrida` (_id d3_rasgos_grafo). Lee: transacciones y
// reglas_deteccion. Idempotente: $merge reemplaza el documento de cada cuenta; con el
// mismo CORTE queda lo mismo (cambian calculado_en y actualizado_en).
//
// Como correrlo:
//   mongosh "<uri>" --quiet --eval 'HORAS=48' --file scripts/procedimientos/02-recalcular-rasgos-grafo.js
//   python tareas.py procedimientos 02-recalcular-rasgos-grafo
//
// Como verificarlo: db.deteccion_estado.find({clase: "rasgo_grafo"}).sort({proporcion_pb: -1})
// o el resumen que imprime. La prueba de equivalencia comprueba que los documentos son
// identicos a los de la funcion de Python (RepoRasgosGrafo.recalcular).
//
// Permisos: centinela_app alcanza ($graphLookup y $match piden find; $merge, borrado y
// reemplazo piden insert, update y remove sobre deteccion_estado).
// ============================================================

const NOMBRE_BASE = typeof BASE_CENTINELA === "string" ? BASE_CENTINELA : "antifraude";
const base = db.getSiblingDB(NOMBRE_BASE);

const ESQUEMA_VERSION = 1;
const NOMBRE_RASGOS = "d3_rasgos_grafo";
const CLASE_RASGO = "rasgo_grafo";
const CLASE_CORRIDA = "rasgo_grafo_corrida";
const SALTOS_MAXIMOS = 6;
const TIPO_REGLA_RED = "red_mulas";

const corte = typeof CORTE === "string" ? new Date(CORTE) : new Date();
if (Number.isNaN(corte.getTime())) throw new Error("CORTE no es una fecha ISO valida: " + CORTE);
const horas = typeof HORAS === "number" ? HORAS : 24;
if (!(horas > 0)) throw new Error("HORAS tiene que ser mayor que 0");
const vigenciaSegundos = typeof VIGENCIA_SEGUNDOS === "number" ? VIGENCIA_SEGUNDOS : 60;
const desde = new Date(corte.getTime() - horas * 3600 * 1000);

let ventanaMinutos;
if (typeof VENTANA_MINUTOS === "number") {
  ventanaMinutos = VENTANA_MINUTOS;
} else {
  const regla = base.reglas_deteccion.findOne({ tipo: TIPO_REGLA_RED }, { parametros: 1 });
  const declarada = regla && regla.parametros ? regla.parametros.ventana_minutos : undefined;
  if (declarada === undefined || declarada === null) {
    throw new Error("no hay regla red_mulas con ventana_minutos; pasar VENTANA_MINUTOS");
  }
  ventanaMinutos = Math.max(1, Number(declarada));
}
if (!(ventanaMinutos >= 1)) throw new Error("VENTANA_MINUTOS tiene que ser al menos 1");

const rango = { $gte: desde, $lt: corte };

// Un recibo r habilita una salida s si r.f <= s.fecha <= r.f + ventana.
const habilita = {
  $anyElementTrue: {
    $map: {
      input: "$recibos",
      as: "r",
      in: {
        $and: [
          { $lte: ["$$r.f", "$$s.fecha"] },
          { $lte: ["$$s.fecha", { $dateAdd: { startDate: "$$r.f", unit: "minute", amount: ventanaMinutos } }] },
        ],
      },
    },
  },
};

const entradaAnterior = {
  $max: {
    $map: {
      input: { $filter: { input: "$recibos", as: "r", cond: { $lte: ["$$r.f", "$$this.fecha"] } } },
      as: "r",
      in: "$$r.f",
    },
  },
};

const pipeline = [
  { $match: { fecha: rango } },
  {
    $group: {
      _id: "$cuenta_destino",
      origenes: { $addToSet: "$cuenta_origen" },
      recibos: { $push: { f: "$fecha" } },
      recibido: { $sum: "$monto_crc" },
    },
  },
  {
    $graphLookup: {
      from: "transacciones",
      startWith: "$_id",
      connectFromField: "cuenta_destino",
      connectToField: "sinpe_origen",
      as: "cadena",
      maxDepth: SALTOS_MAXIMOS - 1,
      depthField: "nivel",
      restrictSearchWithMatch: { fecha: rango },
    },
  },
  { $addFields: { salidas: { $filter: { input: "$cadena", as: "c", cond: { $eq: ["$$c.nivel", 0] } } } } },
  { $addFields: { contadas: { $filter: { input: "$salidas", as: "s", cond: habilita } } } },
  {
    $addFields: {
      reenviado: { $min: [{ $sum: "$contadas.monto_crc" }, "$recibido"] },
      minutos: {
        $ifNull: [
          {
            $max: {
              $map: {
                input: "$contadas",
                in: {
                  $toInt: {
                    $ceil: { $divide: [{ $subtract: ["$$this.fecha", entradaAnterior] }, 60000] },
                  },
                },
              },
            },
          },
          0,
        ],
      },
    },
  },
  {
    $project: {
      _id: { $concat: [NOMBRE_RASGOS + ":", "$_id"] },
      clase: { $literal: CLASE_RASGO },
      disparador: { $literal: NOMBRE_RASGOS },
      cuenta: "$_id",
      ventana_minutos: { $literal: ventanaMinutos },
      desde: { $literal: desde },
      corte: { $literal: corte },
      vigencia_segundos: { $literal: vigenciaSegundos },
      fan_in: { $size: "$origenes" },
      fan_out: { $size: { $setUnion: ["$salidas.cuenta_destino", []] } },
      monto_recibido_crc: { $toLong: "$recibido" },
      monto_reenviado_crc: { $toLong: "$reenviado" },
      proporcion_pb: { $toInt: { $floor: { $divide: [{ $multiply: ["$reenviado", 10000] }, "$recibido"] } } },
      minutos_reenvio: "$minutos",
      saltos: {
        $cond: [{ $eq: [{ $size: "$cadena" }, 0] }, 0, { $add: [{ $toInt: { $max: "$cadena.nivel" } }, 1] }],
      },
      calculado_en: "$$NOW",
      esquema_version: { $literal: ESQUEMA_VERSION },
    },
  },
  { $merge: { into: "deteccion_estado", on: "_id", whenMatched: "replace", whenNotMatched: "insert" } },
];

print("== 02-recalcular-rasgos-grafo (base " + NOMBRE_BASE + ")");
print("rango [" + desde.toISOString() + ", " + corte.toISOString() + "), ventana " + ventanaMinutos + " minutos");

base.transacciones.aggregate(pipeline, { allowDiskUse: true, maxTimeMS: vigenciaSegundos * 1000 }).toArray();

const cuentas = base.deteccion_estado.countDocuments({ clase: CLASE_RASGO, corte: corte });
base.deteccion_estado.replaceOne(
  { _id: NOMBRE_RASGOS },
  {
    _id: NOMBRE_RASGOS,
    clase: CLASE_CORRIDA,
    disparador: NOMBRE_RASGOS,
    ventana_minutos: ventanaMinutos,
    desde: desde,
    corte: corte,
    vigencia_segundos: vigenciaSegundos,
    cuentas: cuentas,
    actualizado_en: new Date(),
    esquema_version: ESQUEMA_VERSION,
  },
  { upsert: true }
);
base.deteccion_estado.deleteMany({ clase: CLASE_RASGO, corte: { $lt: corte } });

print("rasgos de grafo: " + cuentas + " cuenta(s) recalculadas, horizonte " + horas + " h");
base.deteccion_estado.find({ clase: CLASE_RASGO }).sort({ proporcion_pb: -1, monto_recibido_crc: -1 }).limit(5).forEach((f) => {
  print("  " + f.cuenta + "  recibido=" + String(f.monto_recibido_crc).padStart(9) +
    "  reenviado=" + String(f.monto_reenviado_crc).padStart(9) +
    "  proporcion=" + (f.proporcion_pb / 100).toFixed(1) + " %  saltos=" + f.saltos);
});
