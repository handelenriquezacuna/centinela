// ============================================================
// T-76 / H-21 · Procedimiento: recalcular perfiles_comportamiento
//
// Que hace: para cada cliente calcula su perfil con sus transferencias de los ultimos
// DIAS dias y lo escribe con $merge. Es EL MISMO pipeline de app/repos/perfiles.py
// (funcion construir_pipeline); la definicion de cada numero esta en
// app/deteccion/perfiles.py. Lo leen R-01 (monto atipico) y R-05 (horario inusual).
//
// Criterio:
//  - Cuentan las transferencias que el cliente ENVIO (cuenta_origen -> cuentas.cliente_id)
//    en [CORTE - DIAS, CORTE) y que NO tienen una alerta viva: una alerta en estado
//    nueva, en_revision, confirmada o escalada saca a su transaccion del perfil; una
//    descartada (falso positivo) la deja. Asi el fraude no se vuelve "normal" para el
//    motor, y el calculo no lee la etiqueta oculta `simulacion`.
//  - promedio_crc: suma / cantidad, mitad hacia arriba, con aritmetica entera:
//    (2 * suma + n) / (2 * n) truncado. Nunca un flotante; es un long.
//  - maximo_crc: el mayor monto. desviacion_crc: desviacion estandar poblacional,
//    sqrt(n * suma_cuadrados - suma^2) / n, redondeada con floor(x + 0.5).
//  - horario_habitual: la ventana de horas de Costa Rica (UTC-6) mas corta, circular
//    (puede cruzar la medianoche), que reune al menos el 90 % de las transferencias;
//    empate, la que empieza antes. Se escribe HH:00 a HH:59 (22:00 a 06:59, por ejemplo).
//  - destinos_frecuentes: cuentas destino con 2 o mas transferencias, de mas a menos
//    (empate por numero), hasta 10; si ninguna se repite, la mas usada.
//  - num_transferencias: cuantas transferencias contaron.
//  Un cliente sin transferencias que cuenten no recibe perfil nuevo y conserva el que
//  tenia. El documento lleva esquema_version 1 y exactamente los campos del validador
//  (no declara calculado_en, asi que no se escribe).
//
// Entradas (variables globales opcionales, se pasan con --eval):
//   BASE_CENTINELA  base a usar. Por omision "antifraude".
//   DIAS            dias hacia atras desde CORTE. Por omision 90.
//   CLIENTE         un solo cliente, por ejemplo "CLI-001". Por omision, todos.
//   CORTE           fin del rango, ISO 8601 en UTC. Por omision, ahora.
//
// Escribe: perfiles_comportamiento (un documento por cliente, _id = cliente_id).
// Lee: transacciones, cuentas, alertas. Idempotente: $merge reemplaza el documento.
//
// OJO: R-01 y R-05 leen estos perfiles, asi que recalcularlos CAMBIA la deteccion. No
// se corre solo; el recalculo automatico de D3 esta apagado (perfiles.activo en
// config/centinela.yml). Ver docs/06-plataforma.md.
//
// Como correrlo:
//   mongosh "<uri>" --quiet --eval 'DIAS=30; CLIENTE="CLI-001"' \
//     --file scripts/procedimientos/03-recalcular-perfiles.js
//   python tareas.py procedimientos 03-recalcular-perfiles
//
// Como verificarlo: db.perfiles_comportamiento.find({_id: "CLI-001"}) o el resumen que
// imprime. La prueba de equivalencia comprueba que los documentos son identicos a los de
// la funcion de Python (RepoPerfiles.recalcular).
//
// Permisos: centinela_app alcanza (find en tres colecciones; insert y update en perfiles).
// ============================================================

const NOMBRE_BASE = typeof BASE_CENTINELA === "string" ? BASE_CENTINELA : "antifraude";
const base = db.getSiblingDB(NOMBRE_BASE);

const ESQUEMA_VERSION = 1;
const ZONA_CR = "-06:00";
const COBERTURA_HORARIO_PCT = 90;
const MAX_DESTINOS_FRECUENTES = 10;
const MIN_VECES_FRECUENTE = 2;
const HORAS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0"));

const corte = typeof CORTE === "string" ? new Date(CORTE) : new Date();
if (Number.isNaN(corte.getTime())) throw new Error("CORTE no es una fecha ISO valida: " + CORTE);
const dias = typeof DIAS === "number" ? DIAS : 90;
if (!(dias >= 1)) throw new Error("DIAS tiene que ser al menos 1");
const desde = new Date(corte.getTime() - dias * 24 * 3600 * 1000);

let cuentasOrigen = null;
if (typeof CLIENTE === "string") {
  cuentasOrigen = base.cuentas.find({ cliente_id: CLIENTE }, { _id: 1 }).toArray().map((c) => c._id);
  if (cuentasOrigen.length === 0) {
    print("== 03-recalcular-perfiles: el cliente " + CLIENTE + " no tiene cuentas; nada que hacer");
    quit(0);
  }
}

const filtro = { fecha: { $gte: desde, $lt: corte } };
if (cuentasOrigen !== null) filtro.cuenta_origen = { $in: cuentasOrigen };

const n = "$n";
const numerador = { $add: [{ $multiply: [2, "$suma"] }, n] };
const divisor = { $multiply: [2, n] };
const promedio = { $toLong: { $divide: [{ $subtract: [numerador, { $mod: [numerador, divisor] }] }, divisor] } };
const varianzaPorN2 = { $subtract: [{ $multiply: [n, "$suma_cuadrados"] }, { $multiply: ["$suma", "$suma"] }] };
const desviacion = { $toLong: { $floor: { $add: [{ $divide: [{ $sqrt: varianzaPorN2 }, n] }, 0.5] } } };

// Para cada inicio s (0 a 23), el menor largo l (1 a 24) cuya ventana circular reune al
// menos el 90 % de las transferencias; la clave l * 100 + s ordena por largo y, a igual
// largo, por inicio. Las sumas de ventana salen de las sumas acumuladas `pref`.
const cubre = {
  $gte: [
    {
      $multiply: [
        100,
        { $subtract: [{ $arrayElemAt: ["$pref", { $add: ["$$s", "$$l"] }] }, { $arrayElemAt: ["$pref", "$$s"] }] },
      ],
    },
    { $multiply: [COBERTURA_HORARIO_PCT, n] },
  ],
};
const claveVentana = {
  $min: {
    $map: {
      input: { $range: [0, 24] },
      as: "s",
      in: {
        $add: [
          { $multiply: [100, { $min: { $filter: { input: { $range: [1, 25] }, as: "l", cond: cubre } } }] },
          "$$s",
        ],
      },
    },
  },
};

const pipeline = [
  { $match: filtro },
  {
    $lookup: {
      from: "cuentas",
      localField: "cuenta_origen",
      foreignField: "_id",
      pipeline: [{ $project: { cliente_id: 1 } }],
      as: "cuenta",
    },
  },
  { $match: { cuenta: { $ne: [] } } },
  {
    $lookup: {
      from: "alertas",
      localField: "_id",
      foreignField: "transaccion_id",
      pipeline: [{ $match: { estado: { $ne: "descartada" } } }, { $project: { _id: 1 } }],
      as: "alerta_viva",
    },
  },
  { $match: { alerta_viva: { $size: 0 } } },
  {
    $group: {
      _id: { $arrayElemAt: ["$cuenta.cliente_id", 0] },
      n: { $sum: 1 },
      suma: { $sum: "$monto_crc" },
      suma_cuadrados: { $sum: { $multiply: ["$monto_crc", "$monto_crc"] } },
      maximo: { $max: "$monto_crc" },
      horas: { $push: { $hour: { date: "$fecha", timezone: ZONA_CR } } },
      destinos: { $push: "$cuenta_destino" },
    },
  },
  {
    $addFields: {
      hist: {
        $map: {
          input: Array.from({ length: 24 }, (_, h) => h),
          as: "h",
          in: { $size: { $filter: { input: "$horas", as: "x", cond: { $eq: ["$$x", "$$h"] } } } },
        },
      },
      conteo_destinos: {
        $map: {
          input: { $setUnion: ["$destinos", []] },
          as: "d",
          in: {
            cuenta: "$$d",
            veces: { $size: { $filter: { input: "$destinos", as: "x", cond: { $eq: ["$$x", "$$d"] } } } },
          },
        },
      },
    },
  },
  {
    $addFields: {
      pref: {
        $reduce: {
          input: { $concatArrays: ["$hist", "$hist"] },
          initialValue: [0],
          in: { $concatArrays: ["$$value", [{ $add: [{ $arrayElemAt: ["$$value", -1] }, "$$this"] }]] },
        },
      },
      ordenados: { $sortArray: { input: "$conteo_destinos", sortBy: { veces: -1, cuenta: 1 } } },
    },
  },
  { $addFields: { clave_ventana: claveVentana } },
  {
    $addFields: {
      _hora_inicio: { $mod: ["$clave_ventana", 100] },
      _largo: { $divide: [{ $subtract: ["$clave_ventana", { $mod: ["$clave_ventana", 100] }] }, 100] },
      frecuentes: {
        $slice: [
          { $filter: { input: "$ordenados", as: "c", cond: { $gte: ["$$c.veces", MIN_VECES_FRECUENTE] } } },
          MAX_DESTINOS_FRECUENTES,
        ],
      },
    },
  },
  {
    $addFields: {
      _hora_fin: { $mod: [{ $subtract: [{ $add: ["$_hora_inicio", "$_largo"] }, 1] }, 24] },
      frecuentes: {
        $cond: [{ $eq: [{ $size: "$frecuentes" }, 0] }, [{ $arrayElemAt: ["$ordenados", 0] }], "$frecuentes"],
      },
    },
  },
  {
    $project: {
      _id: 1,
      promedio_crc: promedio,
      maximo_crc: "$maximo",
      desviacion_crc: desviacion,
      num_transferencias: "$n",
      horario_habitual: {
        inicio: { $concat: [{ $arrayElemAt: [HORAS, { $toInt: "$_hora_inicio" }] }, ":00"] },
        fin: { $concat: [{ $arrayElemAt: [HORAS, { $toInt: "$_hora_fin" }] }, ":59"] },
      },
      destinos_frecuentes: { $map: { input: "$frecuentes", as: "c", in: "$$c.cuenta" } },
      esquema_version: { $literal: ESQUEMA_VERSION },
    },
  },
  { $merge: { into: "perfiles_comportamiento", on: "_id", whenMatched: "replace", whenNotMatched: "insert" } },
];

print("== 03-recalcular-perfiles (base " + NOMBRE_BASE + ", " + dias + " dias hasta " + corte.toISOString() + ")");
base.transacciones.aggregate(pipeline, { allowDiskUse: true }).toArray(); // $merge no devuelve filas

const alcance = cuentasOrigen === null ? {} : { _id: CLIENTE };
const total = base.perfiles_comportamiento.countDocuments(alcance);
print("perfiles_comportamiento: " + total + " perfil(es) recalculados (" + (cuentasOrigen === null ? "todos los clientes" : CLIENTE) + ", " + dias + " dias)");
base.perfiles_comportamiento.find(alcance).sort({ _id: 1 }).limit(5).forEach((p) => {
  print("  " + p._id + "  n=" + String(p.num_transferencias).padStart(4) +
    "  promedio=" + String(p.promedio_crc).padStart(8) + "  maximo=" + String(p.maximo_crc).padStart(8) +
    "  horario=" + p.horario_habitual.inicio + "-" + p.horario_habitual.fin);
});
