// ============================================================
// Centinela - migracion 001: alertas v1 -> v2 (H-26, Practica 4)
// ============================================================
//
//   python tareas.py migrar
//   mongosh "<uri>" --file scripts/migraciones/001-alertas-v1-a-v2.js
//
// Base: "antifraude". Para otra base (las pruebas), definir BASE_MIGRACION antes:
//   mongosh "<uri>" --eval 'var BASE_MIGRACION="antifraude_pruebas"' --file <este>
//
// Que cambia (decisiones D-01, D-12, D-14):
//   v1  reglas_disparadas: ["REG-001", ...]; version de regla y politica solo
//       como texto en historial[0].nota (lo que escribia D1 antes de H-26).
//   v2  reglas_disparadas: [{regla_id, codigo, version, peso, explicacion}]
//       y politica: {politica_id, version}. esquema_version: 2.
//
// Reejecutable e idempotente: solo toca documentos con esquema_version 1, y
// cada escritura va filtrada por {_id, esquema_version: 1}. Una segunda corrida
// encuentra cero v1 y no cambia nada. El sistema lee las dos versiones mientras
// tanto (app/modelos/alertas.py), asi que se puede correr con D1 arriba.
//
// De donde sale cada dato de v2, en este orden:
//   1. La nota de creacion de D1 ("R-01 REG-001 v1 (+35)", "con POL-001 v1"):
//      es lo que D1 registro al evaluar, y manda.
//   2. Si la nota no lo dice (la ALR-001 sembrada en v1 no lo dice): la version
//      de la regla que regia en fecha_creacion, buscada en versiones_anteriores
//      (H-16) por reemplazada_en; la politica vigente en fecha_creacion.
//   La explicacion se reconstruye desde la regla EN ESA VERSION. Si esa version
//   ya no existe en el documento (la regla se edito y su historia se perdio,
//   por ejemplo al re-sembrar 03-catalogos.js: D-15), la explicacion queda
//   "no disponible: regla editada" y se cuenta. Nunca se inventa con la vigente.
//   Si no hay de donde sacar codigo, version y peso, la alerta queda en v1 y se
//   lista como no migrable: el sistema la sigue leyendo.
// ============================================================

const NOMBRE_BASE = typeof BASE_MIGRACION !== "undefined" ? BASE_MIGRACION : "antifraude";
const base = db.getSiblingDB(NOMBRE_BASE);

const NO_DISPONIBLE = "no disponible: regla editada";
const PATRON_REGLA = /(R-\d{2}) (REG-\d{3,}) v(\d+) \(\+(\d+)\)/g;
const PATRON_POLITICA = /con (POL-\d{3,}) v(\d+)/;

// Lo que D1 dejo escrito en el historial al crear la alerta.
function leerNota(alerta) {
  const versiones = {};
  let politica = null;
  for (const entrada of alerta.historial || []) {
    const nota = entrada.nota || "";
    for (const m of nota.matchAll(PATRON_REGLA)) {
      versiones[m[2]] = { codigo: m[1], version: parseInt(m[3], 10), peso: parseInt(m[4], 10) };
    }
    const p = nota.match(PATRON_POLITICA);
    if (p && politica === null) {
      politica = { politica_id: p[1], version: parseInt(p[2], 10) };
    }
  }
  return { versiones: versiones, politica: politica };
}

// La regla tal como estaba en `version`, o null si esa version ya no existe.
function enVersion(regla, version) {
  if (regla.version === version) return regla;
  return (regla.versiones_anteriores || []).find((v) => v.version === version) || null;
}

// La version que regia en `fecha`: la primera anterior reemplazada DESPUES de la
// fecha; si ninguna, la vigente.
function vigenteEn(regla, fecha) {
  const anteriores = (regla.versiones_anteriores || [])
    .slice()
    .sort((a, b) => a.version - b.version);
  for (const v of anteriores) {
    if (fecha < v.reemplazada_en) return v;
  }
  return regla;
}

function explicar(fuente) {
  return (
    fuente.codigo + " v" + fuente.version + ": " + fuente.descripcion +
    " (aporta " + fuente.peso + "; explicacion reconstruida por la migracion 001)"
  );
}

function politicaEn(fecha) {
  const regia = base.politica_deteccion
    .find({ vigente_desde: { $lte: fecha } })
    .sort({ vigente_desde: -1 })
    .limit(1)
    .toArray()[0] || base.politica_deteccion.findOne({ vigente: true });
  return regia ? { politica_id: regia._id, version: regia.version } : null;
}

const cacheReglas = {};
function regla(reglaId) {
  if (!(reglaId in cacheReglas)) {
    cacheReglas[reglaId] = base.reglas_deteccion.findOne({ _id: reglaId });
  }
  return cacheReglas[reglaId];
}

const yaEnV2 = base.alertas.countDocuments({ esquema_version: 2 });
let migradas = 0;
let sinExplicacion = 0;
const noMigrables = [];

base.alertas.find({ esquema_version: 1 }).forEach((alerta) => {
  const nota = leerNota(alerta);
  const reglas = [];
  let migrable = true;

  for (const reglaId of alerta.reglas_disparadas) {
    const doc = regla(reglaId);
    const deNota = nota.versiones[reglaId];
    let fuente = null;
    if (deNota) {
      fuente = doc ? enVersion(doc, deNota.version) : null;
    } else if (doc) {
      fuente = vigenteEn(doc, alerta.fecha_creacion);
    } else {
      migrable = false;
      break;
    }
    if (fuente) {
      reglas.push({
        regla_id: reglaId,
        codigo: fuente.codigo,
        version: NumberInt(fuente.version),
        peso: NumberInt(fuente.peso),
        explicacion: explicar(fuente)
      });
    } else {
      sinExplicacion += 1;
      reglas.push({
        regla_id: reglaId,
        codigo: deNota.codigo,
        version: NumberInt(deNota.version),
        peso: NumberInt(deNota.peso),
        explicacion: NO_DISPONIBLE
      });
    }
  }

  const politica = nota.politica || politicaEn(alerta.fecha_creacion);
  if (!migrable || politica === null) {
    noMigrables.push(alerta._id);
    return;
  }

  const resultado = base.alertas.updateOne(
    { _id: alerta._id, esquema_version: 1 },
    {
      $set: {
        reglas_disparadas: reglas,
        politica: { politica_id: politica.politica_id, version: NumberInt(politica.version) },
        esquema_version: NumberInt(2)
      }
    }
  );
  migradas += resultado.modifiedCount;
});

print("migracion 001 alertas v1 -> v2 sobre " + NOMBRE_BASE);
print("  migradas: " + migradas);
print("  ya estaban en v2: " + yaEnV2);
print("  reglas con explicacion no disponible (regla editada): " + sinExplicacion);
print("  no migrables, siguen en v1: " + noMigrables.length +
      (noMigrables.length ? " (" + noMigrables.join(", ") + ")" : ""));
