// ============================================================
// T-84 / D-83 · Procedimiento: bitacora de auditoria
//
// Que hace: une en una sola lista, de la mas reciente a la mas antigua, lo que cada coleccion
// ya registra, con un aggregation pipeline y $unionWith. NO crea coleccion (el contrato fija
// 13): la bitacora es una consulta. Es el mismo pipeline que arma
// app/repos/bitacora.py para GET /api/v1/bitacora; la prueba de equivalencia
// (pruebas/integracion/prueba_bitacora_mongo.py) exige las mismas filas.
//
// Forma comun de cada fila: { fecha, actor, accion, objeto, detalle, origen }
//   alertas             historial ($unwind): alerta_<estado>, o la accion cruda (respuesta_cliente)
//   reglas_deteccion    versiones_anteriores, par vN -> vN+1: regla_editada
//   politica_deteccion  idem: politica_editada
//   listas_riesgo       fecha_reporte: lista_riesgo_alta (objeto = la cuenta)
//   casos               fecha_apertura: caso_abierto
//   notificaciones      fecha: notificacion_<pendiente|enviado|fallido> (objeto = la alerta)
// Los respaldos viven como manifiestos en disco, no en Mongo: la API los agrega en Python y
// este procedimiento no los ve. `actor` nulo = la fuente no guarda quien, o lo hizo el sistema.
//
// Entradas (variables globales opcionales, se pasan con --eval):
//   BASE_CENTINELA  base a usar. Por omision "antifraude".
//   DESDE, HASTA    ISO 8601 con zona (2026-11-20T00:00:00Z); limites inclusivos.
//   ACTOR           un agente, AGT-001.
//   ACCION          un tipo de accion o un arreglo de ellos.
//   OBJETO          prefijo del objeto: ALR-, REG-001, 6033-9001.
//   LIMITE          filas (por omision 20, maximo 10100).
//   FORMATO         "tabla" (por omision) o "json" (EJSON canonico, una linea).
//
// Escribe: nada (solo lee). Es reejecutable sin efectos.
//
// Como correrlo:
//   mongosh "<uri>" --quiet --file scripts/procedimientos/06-bitacora.js
//   python tareas.py procedimientos 06
// En Compass: pegar el pipeline de `construirPipeline(...)` en la pestana Aggregations de
// `alertas` (imprimir con FORMATO = "pipeline").
//
// Como verificarlo: devuelve las mismas filas que GET /api/v1/bitacora para los mismos filtros
// (menos los respaldos); la prueba de equivalencia las compara una a una.
//
// Costo: consulta de administracion. Cada rama filtra por fecha, actor y objeto ANTES del
// $unwind cuando puede; el orden y el total se calculan con $facet sobre lo ya filtrado.
// Detalle y explain en docs/06-plataforma.md, "Bitacora de auditoria (T-84)".
//
// Permisos: centinela_app o centinela_analista alcanzan (solo find).
// ============================================================

const NOMBRE_BASE = typeof BASE_CENTINELA === "string" ? BASE_CENTINELA : "antifraude";
const base = db.getSiblingDB(NOMBRE_BASE);

function variable(nombre) {
  return typeof globalThis[nombre] === "undefined" ? null : globalThis[nombre];
}

const filtro = {
  desde: variable("DESDE") ? new Date(variable("DESDE")) : null,
  hasta: variable("HASTA") ? new Date(variable("HASTA")) : null,
  actor: variable("ACTOR"),
  acciones: [].concat(variable("ACCION") || []),
  objeto: variable("OBJETO"),
};
const LIMITE_FILAS = Number(variable("LIMITE") || 20);
const FORMATO = variable("FORMATO") || "tabla";

if (!(LIMITE_FILAS >= 1 && LIMITE_FILAS <= 10100)) throw new Error("LIMITE fuera de 1..10100");
const PATRON = /^[A-Za-z0-9-]{1,32}$/;
if (filtro.actor !== null && !PATRON.test(filtro.actor)) throw new Error("ACTOR invalido");
if (filtro.objeto !== null && !PATRON.test(filtro.objeto)) throw new Error("OBJETO invalido");
filtro.acciones.forEach(function (a) { if (!/^[a-z_]{1,40}$/.test(a)) throw new Error("ACCION invalida: " + a); });
if (filtro.desde && filtro.hasta && filtro.desde > filtro.hasta) throw new Error("DESDE posterior a HASTA");

const ESTADOS_ALERTA = ["nueva", "en_revision", "confirmada", "descartada", "escalada"];
const CAMPOS_REGLA = [["umbral", "peso", "activa"], ["descripcion", "tipo", "parametros"]];
const CAMPOS_POLITICA = [["umbral_alerta", "puntaje_maximo"], ["cortes_severidad", "notificacion"]];

function rango() {
  const r = {};
  if (filtro.desde) r.$gte = filtro.desde;
  if (filtro.hasta) r.$lte = filtro.hasta;
  return Object.keys(r).length ? r : null;
}
function prefijo() { return filtro.objeto !== null ? { $regex: "^" + filtro.objeto } : null; }
function condiciones(pares) {
  const o = {};
  pares.forEach(function (p) { if (p[1] !== null) o[p[0]] = p[1]; });
  return o;
}
function etapaAccion() {
  return filtro.acciones.length ? [{ $match: { accion: { $in: filtro.acciones } } }] : [];
}
function coincidir(c) { return Object.keys(c).length ? [{ $match: c }] : []; }

function ramaAlertas() {
  const r = rango();
  // $elemMatch: la fecha y el actor sobre la MISMA accion, con limites ajustados en los indices.
  const enLaAccion = condiciones([["fecha", r], ["agente_id", filtro.actor]]);
  const poda = condiciones([["_id", prefijo()], ["historial", Object.keys(enLaAccion).length ? { $elemMatch: enLaAccion } : null]]);
  const exacto = condiciones([["historial.fecha", r], ["historial.agente_id", filtro.actor]]);
  exacto.$or = [{ "historial.accion": { $ne: "nueva" } }, { "historial.agente_id": { $ne: null } }];
  const esEstado = { $in: ["$historial.accion", ESTADOS_ALERTA] };
  const anteriores = {
    $filter: {
      input: { $slice: ["$historial_completo", 0, "$indice"] },
      as: "h",
      cond: { $in: ["$$h.accion", ESTADOS_ALERTA] },
    },
  };
  const nota = { $ifNull: ["$historial.nota", ""] };
  return [].concat(
    coincidir(poda),
    [
      { $set: { historial_completo: "$historial" } },
      { $unwind: { path: "$historial", includeArrayIndex: "indice" } },
      { $match: exacto },
      {
        $set: {
          anterior: {
            $cond: [
              { $gt: ["$indice", 0] },
              { $arrayElemAt: [{ $map: { input: anteriores, as: "h", in: "$$h.accion" } }, -1] },
              null,
            ],
          },
        },
      },
      {
        $project: {
          _id: 0,
          fecha: "$historial.fecha",
          actor: { $ifNull: ["$historial.agente_id", null] },
          accion: { $cond: [esEstado, { $concat: ["alerta_", "$historial.accion"] }, "$historial.accion"] },
          objeto: "$_id",
          detalle: {
            $cond: [
              esEstado,
              {
                $concat: [
                  { $ifNull: ["$anterior", "?"] },
                  " -> ",
                  "$historial.accion",
                  { $cond: [{ $eq: [nota, ""] }, "", { $concat: [": ", nota] }] },
                ],
              },
              nota,
            ],
          },
          origen: { $literal: "alertas" },
        },
      },
    ],
    etapaAccion()
  );
}

function cambios(campos) {
  const frases = [];
  campos[0].forEach(function (campo) {
    const antes = "$$a." + campo, despues = "$$b." + campo;
    frases.push({
      $cond: [
        { $ne: [antes, despues] },
        { $concat: [campo, " ", { $ifNull: [{ $toString: antes }, "-"] }, " -> ", { $ifNull: [{ $toString: despues }, "-"] }] },
        null,
      ],
    });
  });
  campos[1].forEach(function (campo) {
    frases.push({ $cond: [{ $ne: ["$$a." + campo, "$$b." + campo] }, campo + " cambio", null] });
  });
  const unidas = {
    $reduce: {
      input: { $filter: { input: frases, as: "f", cond: { $ne: ["$$f", null] } } },
      initialValue: "",
      in: { $concat: ["$$value", { $cond: [{ $eq: ["$$value", ""] }, "", ", "] }, "$$this"] },
    },
  };
  return { $cond: [{ $eq: [unidas, ""] }, "sin cambios en los campos comparados", unidas] };
}

function ramaVersiones(origen, accion, campos, rotulo) {
  const r = rango();
  const poda = condiciones([["_id", prefijo()], ["versiones_anteriores.reemplazada_en", r || { $type: "date" }]]);
  const exacto = condiciones([["pares.a.reemplazada_en", r], ["pares.a.agente_id", filtro.actor]]);
  const vigente = { version: "$version" };
  campos[0].concat(campos[1]).forEach(function (campo) { vigente[campo] = "$" + campo; });
  return [].concat(
    coincidir(poda),
    [
      { $set: { lineas: { $concatArrays: ["$versiones_anteriores", [vigente]] } } },
      {
        $set: {
          pares: {
            $map: {
              input: { $range: [0, { $size: "$versiones_anteriores" }] },
              as: "i",
              in: {
                a: { $arrayElemAt: ["$lineas", "$$i"] },
                b: { $arrayElemAt: ["$lineas", { $add: ["$$i", 1] }] },
              },
            },
          },
        },
      },
      { $unwind: "$pares" },
    ],
    coincidir(exacto),
    [
      {
        $project: {
          _id: 0,
          fecha: "$pares.a.reemplazada_en",
          actor: { $ifNull: ["$pares.a.agente_id", null] },
          accion: { $literal: accion },
          objeto: "$_id",
          detalle: {
            $let: {
              vars: { a: "$pares.a", b: "$pares.b" },
              in: { $concat: [rotulo, " v", { $toString: "$$a.version" }, " -> v", { $toString: "$$b.version" }, ": ", cambios(campos)] },
            },
          },
          origen: { $literal: origen },
        },
      },
    ],
    etapaAccion()
  );
}

function ramaListasRiesgo() {
  const pre = condiciones([["fecha_reporte", rango() || { $type: "date" }], ["cuenta", prefijo()], ["agente_id", filtro.actor]]);
  return [].concat(
    [
      { $match: pre },
      {
        $project: {
          _id: 0,
          fecha: "$fecha_reporte",
          actor: { $ifNull: ["$agente_id", null] },
          accion: { $literal: "lista_riesgo_alta" },
          objeto: "$cuenta",
          detalle: { $concat: ["$_id", ": ", "$motivo"] },
          origen: { $literal: "listas_riesgo" },
        },
      },
    ],
    etapaAccion()
  );
}

function ramaCasos() {
  const pre = condiciones([["fecha_apertura", rango() || { $type: "date" }], ["_id", prefijo()], ["agente_id", filtro.actor]]);
  const unidas = {
    $reduce: {
      input: "$alertas",
      initialValue: "",
      in: { $concat: ["$$value", { $cond: [{ $eq: ["$$value", ""] }, "", ", "] }, "$$this"] },
    },
  };
  return [].concat(
    [
      { $match: pre },
      {
        $project: {
          _id: 0,
          fecha: "$fecha_apertura",
          actor: { $ifNull: ["$agente_id", null] },
          accion: { $literal: "caso_abierto" },
          objeto: "$_id",
          detalle: {
            $concat: [
              { $toString: { $size: "$alertas" } },
              " alerta(s): ",
              unidas,
              { $cond: [{ $gt: ["$cuenta_destino", null] }, { $concat: [" hacia ", "$cuenta_destino"] }, ""] },
            ],
          },
          origen: { $literal: "casos" },
        },
      },
    ],
    etapaAccion()
  );
}

function ramaNotificaciones() {
  const pre = condiciones([["fecha", rango()], ["alerta_id", prefijo()], ["agente_id", filtro.actor]]);
  const error = { $ifNull: ["$detalle_error", ""] };
  return [].concat(
    coincidir(pre),
    [
      {
        $project: {
          _id: 0,
          fecha: "$fecha",
          actor: { $ifNull: ["$agente_id", null] },
          accion: { $concat: ["notificacion_", "$estado_envio"] },
          objeto: "$alerta_id",
          detalle: {
            $concat: ["$_id", " por ", "$canal", { $cond: [{ $eq: [error, ""] }, "", { $concat: [": ", error] }] }],
          },
          origen: { $literal: "notificaciones" },
        },
      },
    ],
    etapaAccion()
  );
}

const ORDEN = { fecha: -1, objeto: -1, accion: 1, actor: 1, detalle: 1 };

function construirPipeline(tope) {
  const uniones = [
    ["reglas_deteccion", ramaVersiones("reglas_deteccion", "regla_editada", CAMPOS_REGLA, "regla")],
    ["politica_deteccion", ramaVersiones("politica_deteccion", "politica_editada", CAMPOS_POLITICA, "politica")],
    ["listas_riesgo", ramaListasRiesgo()],
    ["casos", ramaCasos()],
    ["notificaciones", ramaNotificaciones()],
  ];
  return [].concat(
    ramaAlertas(),
    uniones.map(function (u) { return { $unionWith: { coll: u[0], pipeline: u[1] } }; }),
    [{ $facet: { pagina: [{ $sort: ORDEN }, { $limit: tope }], total: [{ $count: "n" }] } }]
  );
}

if (FORMATO === "pipeline") {
  print(EJSON.stringify(construirPipeline(LIMITE_FILAS), null, 2));
} else {
  const resultado = base.alertas.aggregate(construirPipeline(LIMITE_FILAS), { allowDiskUse: true }).toArray()[0];
  const filas = resultado.pagina;
  const total = resultado.total.length ? resultado.total[0].n : 0;
  if (FORMATO === "json") {
    print(EJSON.stringify({ total: total, filas: filas }, null, 0, { relaxed: false }));
  } else {
    print("Bitacora de auditoria: " + filas.length + " de " + total + " fila(s) (mas recientes primero)");
    filas.forEach(function (f) {
      print(f.fecha.toISOString() + "  " + String(f.actor === null ? "-" : f.actor).padEnd(8) + "  " +
        f.accion.padEnd(24) + "  " + String(f.objeto).padEnd(12) + "  " + f.detalle);
    });
    print("Los respaldos (manifiestos en disco) no se ven aqui: los agrega la API en Python.");
  }
}
