// ============================================================
// T-76 / H-22 · Procedimiento: recalcular indicadores_diarios
//
// Que hace: materializa `indicadores_diarios` (vista materializada) a partir de
// `alertas` para un rango de dias de Costa Rica. Es EL MISMO pipeline que corre el
// motor en Python (app/repos/indicadores.py, funcion construir_pipeline): $match,
// $addFields, $group, $sort, $project y $merge. Un dia del rango sin alertas se
// escribe en ceros (solo si no existe, con un upsert de pipeline), para que el tablero lea un
// cero y no un hueco.
//
// Entradas (variables globales opcionales, se pasan con --eval):
//   BASE_CENTINELA  base a usar. Por omision "antifraude".
//   DESDE           primer dia, texto YYYY-MM-DD (dia de Costa Rica). Por omision, hoy.
//   HASTA           ultimo dia, YYYY-MM-DD, incluido. Por omision, igual a DESDE.
//   DESDE y HASTA van juntos o ninguno.
//
// Escribe: indicadores_diarios (un documento por dia, _id = la fecha). Lee: alertas.
// Es idempotente: el _id es la fecha y $merge reemplaza el documento entero; correrlo
// dos veces deja los mismos documentos (solo cambia calculado_en).
//
// Como correrlo:
//   mongosh "<uri>" --quiet --eval 'DESDE="2026-11-01"; HASTA="2026-11-22"' \
//     --file scripts/procedimientos/01-recalcular-indicadores.js
//   python tareas.py procedimientos 01-recalcular-indicadores --desde 2026-11-01 --hasta 2026-11-22
//
// Como verificarlo: db.indicadores_diarios.find({_id: {$gte: DESDE, $lte: HASTA}}) o
// las lineas que imprime el script al final. Una prueba de integracion
// (pruebas/integracion/prueba_procedimientos_mongo.py) comprueba que sus documentos son
// identicos, byte a byte salvo calculado_en, a los de la funcion de Python.
//
// Permisos: centinela_app alcanza (find sobre alertas; insert y update sobre
// indicadores_diarios, que es lo que pide $merge).
// ============================================================

const NOMBRE_BASE = typeof BASE_CENTINELA === "string" ? BASE_CENTINELA : "antifraude";
const base = db.getSiblingDB(NOMBRE_BASE);

const ESQUEMA_VERSION = 1;
const DESFASE_CR_MS = 6 * 3600 * 1000; // Costa Rica: UTC-6 fijo, sin horario de verano
const ZONA_CR = "-06:00";
const DIA_MS = 24 * 3600 * 1000;
const SEVERIDADES = ["MEDIA", "ALTA", "CRITICA"];
const ESTADOS = ["nueva", "en_revision", "confirmada", "descartada", "escalada"];
const ESTADOS_TERMINALES = ["confirmada", "descartada", "escalada"];
const ESTADOS_PROTEGEN = ["confirmada", "escalada"];
const ESTADOS_PENDIENTES = ["nueva", "en_revision"];
const SIN_ASIGNAR = "sin_asignar";
const HORAS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0"));

function fechaCR(ms) {
  return new Date(ms - DESFASE_CR_MS).toISOString().slice(0, 10);
}

function comoDia(valor, nombre) {
  if (typeof valor !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(valor)) {
    throw new Error(nombre + " debe ser texto YYYY-MM-DD, llego: " + valor);
  }
  const ms = Date.parse(valor + "T00:00:00-06:00");
  if (Number.isNaN(ms) || fechaCR(ms) !== valor) throw new Error(nombre + " no es una fecha valida: " + valor);
  return ms; // medianoche de ese dia en Costa Rica, en milisegundos UTC
}

const hayDesde = typeof DESDE === "string";
const hayHasta = typeof HASTA === "string";
if (hayDesde !== hayHasta) throw new Error("DESDE y HASTA van juntos");
const inicioMs = hayDesde ? comoDia(DESDE, "DESDE") : comoDia(fechaCR(Date.now()), "hoy");
const ultimoMs = hayHasta ? comoDia(HASTA, "HASTA") : inicioMs;
if (ultimoMs < inicioMs) throw new Error("HASTA no puede ser anterior a DESDE");

const inicio = new Date(inicioMs);
const fin = new Date(ultimoMs + DIA_MS);
const dias = [];
for (let ms = inicioMs; ms <= ultimoMs; ms += DIA_MS) dias.push(fechaCR(ms));

function contar(campo, valor) {
  return { $sum: { $cond: [{ $eq: ["$" + campo, valor] }, 1, 0] } };
}

// Segundos de fecha_creacion a la primera transicion terminal del historial; null si la
// alerta sigue abierta; nunca negativo.
const segundosHastaDecision = {
  $let: {
    vars: {
      primera: {
        $min: {
          $map: {
            input: { $filter: { input: "$historial", cond: { $in: ["$$this.accion", ESTADOS_TERMINALES] } } },
            in: "$$this.fecha",
          },
        },
      },
    },
    in: {
      $cond: [
        { $eq: ["$$primera", null] },
        null,
        { $max: [0, { $dateDiff: { startDate: "$fecha_creacion", endDate: "$$primera", unit: "second" } }] },
      ],
    },
  },
};

const pendiente = { $in: ["$estado", ESTADOS_PENDIENTES] };
const grupo = {
  _id: { $dateToString: { format: "%Y-%m-%d", date: "$fecha_creacion", timezone: ZONA_CR } },
  total: { $sum: 1 },
  agentes: { $push: { $ifNull: ["$agente_id", SIN_ASIGNAR] } },
  horas: { $push: { $dateToString: { format: "%H", date: "$fecha_creacion", timezone: ZONA_CR } } },
  decision_cantidad: { $sum: { $cond: [{ $ne: ["$_decision_s", null] }, 1, 0] } },
  decision_segundos_total: { $sum: "$_decision_s" },
  monto_protegido: { $sum: { $cond: [{ $in: ["$estado", ESTADOS_PROTEGEN] }, "$monto_crc", 0] } },
  pendientes: { $sum: { $cond: [pendiente, 1, 0] } },
  pendiente_antigua: { $min: { $cond: [pendiente, "$fecha_creacion", null] } },
};
SEVERIDADES.forEach((s) => { grupo["sev_" + s] = contar("severidad", s); });
ESTADOS.forEach((e) => { grupo["est_" + e] = contar("estado", e); });

function conteoPorClave(entrada, claves) {
  return {
    $arrayToObject: {
      $map: {
        input: claves,
        as: "k",
        in: { k: "$$k", v: { $size: { $filter: { input: entrada, cond: { $eq: ["$$this", "$$k"] } } } } },
      },
    },
  };
}

const pipeline = [
  { $match: { fecha_creacion: { $gte: inicio, $lt: fin } } },
  { $addFields: { _decision_s: segundosHastaDecision } },
  { $group: grupo },
  { $sort: { _id: 1 } },
  {
    $project: {
      _id: 1,
      fecha: "$_id",
      total_alertas: "$total",
      por_severidad: Object.fromEntries(SEVERIDADES.map((s) => [s, "$sev_" + s])),
      por_estado: Object.fromEntries(ESTADOS.map((e) => [e, "$est_" + e])),
      por_agente: {
        $arrayToObject: {
          $map: {
            input: { $setUnion: ["$agentes", []] },
            as: "a",
            in: { k: "$$a", v: { $size: { $filter: { input: "$agentes", cond: { $eq: ["$$this", "$$a"] } } } } },
          },
        },
      },
      por_hora: conteoPorClave("$horas", HORAS),
      decision_cantidad: "$decision_cantidad",
      decision_segundos_total: "$decision_segundos_total",
      monto_protegido_crc: "$monto_protegido",
      pendientes: "$pendientes",
      pendiente_mas_antigua: { $cond: [{ $eq: ["$pendiente_antigua", null] }, "$$REMOVE", "$pendiente_antigua"] },
      calculado_en: "$$NOW",
      esquema_version: { $literal: ESQUEMA_VERSION },
    },
  },
  { $merge: { into: "indicadores_diarios", on: "_id", whenMatched: "replace", whenNotMatched: "insert" } },
];

// Un dia sin alertas, en ceros, escrito SOLO si no existe (una actualizacion con pipeline y
// upsert: si el documento ya tiene `fecha` se queda como esta). Se arma con $arrayToObject
// y no con un objeto de JavaScript porque JS reordena las claves que parecen enteros
// ("10" antes de "00") y por_hora tiene que quedar de "00" a "23". Los campos van en orden
// alfabetico, que es como los deja el upsert de la funcion de Python.
const ceros = (claves) => ({ $arrayToObject: [claves.map((k) => ({ k: k, v: 0 }))] });
const documentoEnCeros = (fecha) => ({
  _id: "$_id",
  calculado_en: "$$NOW",
  decision_cantidad: { $literal: 0 },
  decision_segundos_total: { $literal: 0 },
  esquema_version: { $literal: ESQUEMA_VERSION },
  fecha: { $literal: fecha },
  monto_protegido_crc: { $literal: 0 },
  pendientes: { $literal: 0 },
  por_agente: { $literal: {} },
  por_estado: ceros(ESTADOS),
  por_hora: ceros(HORAS),
  por_severidad: ceros(SEVERIDADES),
  total_alertas: { $literal: 0 },
});

print("== 01-recalcular-indicadores (base " + NOMBRE_BASE + ", " + dias[0] + " a " + dias[dias.length - 1] + ")");
base.alertas.aggregate(pipeline).toArray(); // $merge no devuelve filas; esto lo ejecuta

dias.forEach((fecha) => {
  base.indicadores_diarios.updateOne(
    { _id: fecha },
    [{ $replaceWith: { $cond: [{ $eq: [{ $type: "$fecha" }, "string"] }, "$$ROOT", documentoEnCeros(fecha)] } }],
    { upsert: true }
  );
});

print("indicadores_diarios: " + dias.length + " dia(s) recalculados");
base.indicadores_diarios.find({ _id: { $in: dias } }).sort({ _id: 1 }).forEach((d) => {
  print("  " + d.fecha + "  alertas=" + String(d.total_alertas).padStart(5) +
    "  severidad=" + JSON.stringify(d.por_severidad) + "  pendientes=" + d.pendientes);
});
