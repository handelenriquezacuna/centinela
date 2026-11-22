// ============================================================
// Centinela - actualizar validadores sin borrar datos (T-24)
// ============================================================
//
// Problema: 01-colecciones.js es la fuente de verdad de los validadores, pero para
// aplicarlos hace drop + create de las colecciones de dominio. Cuando el validador
// cambia en el codigo (una rama nueva, un campo nuevo) y la base local conserva el
// viejo, la unica salida era re-sembrar, y eso borra los datos. Sintoma real: la API
// de reglas respondio 500 porque el validador viejo rechazaba la escritura (codigo 121).
//
// Que hace, sin modificar 01-colecciones.js:
//   1. Carga 01-colecciones.js contra una base TEMPORAL (antifraude_validadores_tmp):
//      reemplaza db.getSiblingDB para que el script crea que escribe en "antifraude".
//   2. Lee options.validator, validationLevel y validationAction de cada coleccion de
//      la temporal.
//   3. Aplica collMod sobre la base real solo donde el validador difiere. collMod no
//      toca documentos; si la coleccion no existe todavia, la crea vacia con su
//      validador.
//   4. Borra la temporal (siempre, aun si algo falla) y reporta cuantas actualizo.
//
// Uso (python tareas.py validadores lo corre con la URI completa):
//   mongosh "<uri>" --file scripts/semilla/actualizar-validadores.js
//
// Variables opcionales, definidas antes con --eval (como en las migraciones):
//   BASE_VALIDADORES  base a actualizar (por omision "antifraude"; las pruebas)
//   RUTA_COLECCIONES  ruta de 01-colecciones.js (por omision relativa a la raiz)
// ============================================================

const BASE_DESTINO = typeof BASE_VALIDADORES !== "undefined" ? BASE_VALIDADORES : "antifraude";
const BASE_TEMPORAL = "antifraude_validadores_tmp";
const RUTA_01 = typeof RUTA_COLECCIONES !== "undefined"
  ? RUTA_COLECCIONES
  : "scripts/semilla/01-colecciones.js";

if (BASE_DESTINO === BASE_TEMPORAL) {
  throw new Error("la base destino no puede ser la temporal");
}

const conexion = db.getMongo();
const destino = conexion.getDB(BASE_DESTINO);
const temporal = conexion.getDB(BASE_TEMPORAL);

// Si quedo una temporal de una corrida interrumpida, se descarta: es solo andamio.
temporal.dropDatabase();

function opcionesDe(base, nombre) {
  const info = base.getCollectionInfos({ name: nombre })[0];
  return info ? (info.options || {}) : null;
}

function igual(a, b) {
  return EJSON.stringify(a || null) === EJSON.stringify(b || null);
}

let actualizadas = 0;
let alDia = 0;
let creadas = 0;

try {
  // 01-colecciones.js hace `db = db.getSiblingDB("antifraude")`. Se reemplaza el metodo
  // en el objeto `db` para que esa llamada devuelva la temporal. No se toca el archivo.
  const original = db.getSiblingDB;
  const imprimir = print;
  db.getSiblingDB = function () { return temporal; };
  print = function () {};  // 01 imprime un resumen de la base temporal: es ruido aqui
  try {
    load(RUTA_01);
  } finally {
    print = imprimir;
    db = conexion.getDB(BASE_DESTINO);
    if (db.getSiblingDB !== original) {
      delete db.getSiblingDB;
    }
  }

  temporal.getCollectionInfos({ type: "collection" }).forEach(function (info) {
    const opciones = info.options || {};
    if (!opciones.validator) {
      return;
    }
    const nombre = info.name;
    const deseadas = {
      validator: opciones.validator,
      validationLevel: opciones.validationLevel || "strict",
      validationAction: opciones.validationAction || "error"
    };

    const actuales = opcionesDe(destino, nombre);
    if (actuales === null) {
      destino.createCollection(nombre, deseadas);
      creadas += 1;
      print("  creada (no existia)  " + nombre);
      return;
    }
    if (igual(actuales.validator, deseadas.validator)
        && (actuales.validationLevel || "strict") === deseadas.validationLevel
        && (actuales.validationAction || "error") === deseadas.validationAction) {
      alDia += 1;
      return;
    }
    const resultado = destino.runCommand(Object.assign({ collMod: nombre }, deseadas));
    if (resultado.ok !== 1) {
      throw new Error("collMod fallo en " + nombre + ": " + tojson(resultado));
    }
    actualizadas += 1;
    print("  actualizada          " + nombre);
  });
} finally {
  temporal.dropDatabase();
}

print("validadores en " + BASE_DESTINO + ": " + actualizadas + " actualizados, "
  + creadas + " creados, " + alDia + " ya al dia. Documentos intactos; base temporal borrada.");
