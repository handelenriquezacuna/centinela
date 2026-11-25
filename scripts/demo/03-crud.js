// ============================================================
// Centinela - demo mongosh, paso 3: insercion, modificacion y eliminacion
// ============================================================
//
// Las escrituras que hace la API, pero desde mongosh, sobre documentos de la semilla:
//   (a) updateOne con versionado: el umbral de R-01 sube de 8 a 9 y vuelve a 8.
//       Es la misma forma que app/repos/reglas.py (editar): la version sube en uno, la
//       version anterior entra completa a versiones_anteriores, y el filtro lleva la
//       version leida (concurrencia optimista).
//   (b) updateOne con $push: una alerta en estado nueva pasa a en_revision, asigna
//       agente y gana una entrada en el historial (app/repos/alertas.py,
//       planear_transicion). Despues, un intento de transicion invalida con filtro
//       condicionado: matchedCount 0, nada se escribe.
//   (c) insertOne y deleteOne sobre listas_riesgo, con un documento de prueba.
//
// Al final deja la base como estaba: la regla con su version e historia de antes, la
// alerta en nueva y sin agente, y listas_riesgo sin el documento de prueba. Si el
// script se corta a medias, revisar a mano antes de volver a correrlo. Con la app
// corriendo, el panel muestra el cambio de (b) y su reversa por el change stream.
//
// No necesita la app para escribir. Requiere las semillas 01 a 03. Para (b), hace
// falta al menos una alerta en estado nueva sin agente; si no hay, (b) se omite y lo dice.
//
// Uso (desde la raiz del repo):
//   mongosh "$URI_DEMO" --file scripts/demo/03-crud.js

load("scripts/demo/00-ayudas.js");

// Numero de un valor leido de la base, sea Int32, Long o double.
function aNumero(valor) {
  if (valor !== null && typeof valor === "object" && typeof valor.toNumber === "function") {
    return valor.toNumber();
  }
  return Number(valor);
}

// Mismo tipo BSON que el valor original: Long si lo era, Int32 si no.
function comoDelTipo(original, numero) {
  if (original !== null && typeof original === "object" && original._bsontype === "Long") {
    return NumberLong(numero);
  }
  return NumberInt(numero);
}

// Copia de TRANSICIONES en app/modelos/estados.py. Si cambia alla, cambia aca.
const TRANSICIONES = {
  nueva: ["en_revision"],
  en_revision: ["confirmada", "descartada", "escalada"],
  confirmada: [],
  descartada: [],
  escalada: []
};

function transicionPermitida(origen, destino) {
  return TRANSICIONES[origen].indexOf(destino) !== -1;
}

// ------------------------------------------------------------
// (a) updateOne con versionado: umbral de R-01
// ------------------------------------------------------------
print("== (a) updateOne: umbral de R-01 con versionado ==");

// Edita el umbral como app/repos/reglas.py (editar): la instantanea es el documento
// completo menos _id y versiones_anteriores, mas reemplazada_en. El filtro lleva la
// version leida; si otro la cambio antes, matchedCount es 0.
function editarUmbral(actual, nuevoValor) {
  const ahora = new Date();
  const versionLeida = aNumero(actual.version);
  const instantanea = Object.assign({}, actual);
  delete instantanea._id;
  delete instantanea.versiones_anteriores;
  instantanea.reemplazada_en = ahora;
  const resultado = db.reglas_deteccion.updateOne(
    { _id: actual._id, version: NumberInt(versionLeida) },
    {
      $set: {
        umbral: comoDelTipo(actual.umbral, nuevoValor),
        version: NumberInt(versionLeida + 1)
      },
      $push: { versiones_anteriores: instantanea }
    }
  );
  print("updateOne: matchedCount " + resultado.matchedCount + ", modifiedCount " + resultado.modifiedCount);
  return ahora;
}

const regla0 = db.reglas_deteccion.findOne({ codigo: "R-01" });
let fechaEdicion1 = null;
let fechaEdicion2 = null;

if (regla0 === null) {
  print("No hay regla R-01 en reglas_deteccion. Correr la semilla 03. Paso (a) omitido.");
} else {
  const UMBRAL_ORIGINAL = regla0.umbral;
  const VERSION_ORIGINAL = regla0.version;
  const HISTORIA_ORIGINAL = (regla0.versiones_anteriores || []).length;

  print("antes:       umbral " + aNumero(regla0.umbral) + " | version " +
    aNumero(regla0.version) + " | versiones_anteriores " + HISTORIA_ORIGINAL);

  // Edicion 1: el umbral sube de 8 a 9.
  fechaEdicion1 = editarUmbral(regla0, 9);
  const regla1 = db.reglas_deteccion.findOne({ _id: regla0._id });
  print("tras subir:  umbral " + aNumero(regla1.umbral) + " | version " +
    aNumero(regla1.version) + " | versiones_anteriores " + regla1.versiones_anteriores.length);

  // Edicion 2: se vuelve al valor original, tambien como version nueva.
  fechaEdicion2 = editarUmbral(regla1, aNumero(UMBRAL_ORIGINAL));
  const regla2 = db.reglas_deteccion.findOne({ _id: regla0._id });
  print("tras volver: umbral " + aNumero(regla2.umbral) + " | version " +
    aNumero(regla2.version) + " | versiones_anteriores " + regla2.versiones_anteriores.length);

  // Limpieza: umbral y version originales, y fuera las dos entradas de esta demo.
  const limpieza = db.reglas_deteccion.updateOne(
    { _id: regla0._id, version: regla2.version },
    {
      $set: { umbral: UMBRAL_ORIGINAL, version: VERSION_ORIGINAL },
      $pull: { versiones_anteriores: { reemplazada_en: { $in: [fechaEdicion1, fechaEdicion2] } } }
    }
  );
  print("limpieza:    matchedCount " + limpieza.matchedCount + ", modifiedCount " + limpieza.modifiedCount);

  const regla3 = db.reglas_deteccion.findOne({ _id: regla0._id });
  print("despues:     umbral " + aNumero(regla3.umbral) + " | version " +
    aNumero(regla3.version) + " | versiones_anteriores " + (regla3.versiones_anteriores || []).length +
    " (era " + HISTORIA_ORIGINAL + ")");
}

// ------------------------------------------------------------
// (b) updateOne con $push: alerta nueva -> en_revision
// ------------------------------------------------------------
print("");
print("== (b) updateOne con $push: transicion de una alerta ==");

const AGENTE = "AGT-001";
const NOTA_VALIDA = "demo CRUD: tomada para revision";
const NOTA_INVALIDA = "demo CRUD: intento invalido";

// La misma consulta que usa planear_transicion: estado de origen y sin agente.
const alerta0 = db.alertas.findOne(
  { estado: "nueva", agente_id: { $exists: false } },
  { _id: 1, estado: 1, historial: 1 }
);

if (alerta0 === null) {
  print("No hay alerta en estado nueva sin agente. Paso (b) omitido.");
} else {
  const ALR = alerta0._id;
  print("antes: " + ALR + " | estado " + alerta0.estado + " | historial " +
    alerta0.historial.length + " entradas | sin agente_id");

  // (b1) Transicion valida: nueva -> en_revision. El filtro pide el estado de origen y
  // la ausencia de agente. $set asigna el agente; $push agrega la entrada.
  print("tabla: nueva -> en_revision permitido: " + transicionPermitida("nueva", "en_revision"));
  const valida = db.alertas.updateOne(
    { _id: ALR, estado: "nueva", agente_id: { $exists: false } },
    {
      $set: { estado: "en_revision", agente_id: AGENTE },
      $push: {
        historial: {
          accion: "en_revision",
          agente_id: AGENTE,
          fecha: new Date(),
          nota: NOTA_VALIDA
        }
      }
    }
  );
  print("transicion valida: matchedCount " + valida.matchedCount + ", modifiedCount " + valida.modifiedCount);

  // (b2) Transicion invalida: nueva -> confirmada no esta en la tabla. La API la
  // rechaza antes de escribir (exigir_transicion). Aca se ve lo que pasa si la
  // escritura saliera igual: el filtro pide estado "nueva", y la alerta ya no esta
  // ahi, asi que no encuentra nada y no escribe.
  print("tabla: nueva -> confirmada permitido: " + transicionPermitida("nueva", "confirmada"));
  const invalida = db.alertas.updateOne(
    { _id: ALR, estado: "nueva" },
    {
      $set: { estado: "confirmada" },
      $push: {
        historial: {
          accion: "confirmada",
          agente_id: AGENTE,
          fecha: new Date(),
          nota: NOTA_INVALIDA
        }
      }
    }
  );
  print("transicion invalida con filtro condicionado: matchedCount " + invalida.matchedCount +
    ", modifiedCount " + invalida.modifiedCount);

  const trasInvalida = db.alertas.findOne({ _id: ALR }, { estado: 1, historial: 1 });
  print("despues del intento: estado " + trasInvalida.estado + " | historial " +
    trasInvalida.historial.length + " entradas");

  // Limpieza: la alerta vuelve a nueva, sin agente, sin las entradas de esta demo.
  const limpiezaAlerta = db.alertas.updateOne(
    { _id: ALR },
    {
      $set: { estado: "nueva" },
      $unset: { agente_id: "" },
      $pull: { historial: { nota: { $in: [NOTA_VALIDA, NOTA_INVALIDA] } } }
    }
  );
  print("limpieza: matchedCount " + limpiezaAlerta.matchedCount + ", modifiedCount " +
    limpiezaAlerta.modifiedCount);

  const alertaFinal = db.alertas.findOne({ _id: ALR }, { estado: 1, agente_id: 1, historial: 1 });
  print("despues: " + ALR + " | estado " + alertaFinal.estado + " | agente_id " +
    (alertaFinal.agente_id === undefined ? "ausente" : alertaFinal.agente_id) +
    " | historial " + alertaFinal.historial.length + " entradas (era " +
    alerta0.historial.length + ")");
}

// ------------------------------------------------------------
// (c) insertOne y deleteOne sobre listas_riesgo
// ------------------------------------------------------------
print("");
print("== (c) insertOne y deleteOne: listas_riesgo ==");

const PRUEBA = {
  _id: "RSK-990",
  cuenta: "7000-0990",
  motivo: "Demo CRUD: entrada de prueba, se borra en este mismo script",
  fecha_reporte: new Date(),
  vigente: true,
  esquema_version: 1
};

if (db.listas_riesgo.findOne({ $or: [{ _id: PRUEBA._id }, { cuenta: PRUEBA.cuenta }] }) !== null) {
  print("Ya existe RSK-990 o la cuenta 7000-0990 en listas_riesgo. Paso (c) omitido.");
} else {
  print("Documento a insertar:");
  printjson(PRUEBA);

  const insercion = db.listas_riesgo.insertOne(PRUEBA);
  print("insertOne acknowledged: " + insercion.acknowledged + ", insertedId: " + insercion.insertedId);
  print("listas_riesgo con cuenta " + PRUEBA.cuenta + ": " +
    db.listas_riesgo.countDocuments({ cuenta: PRUEBA.cuenta }));

  const borrado = db.listas_riesgo.deleteOne({ _id: PRUEBA._id, motivo: PRUEBA.motivo });
  print("deleteOne deletedCount: " + borrado.deletedCount);
  print("listas_riesgo con cuenta " + PRUEBA.cuenta + " despues: " +
    db.listas_riesgo.countDocuments({ cuenta: PRUEBA.cuenta }));
}

print("");
print("== Estado final ==");
const reglaFinal = db.reglas_deteccion.findOne({ codigo: "R-01" }, { umbral: 1, version: 1, versiones_anteriores: 1 });
if (reglaFinal !== null) {
  print("R-01: umbral " + aNumero(reglaFinal.umbral) + " | version " + aNumero(reglaFinal.version) +
    " | versiones_anteriores " + (reglaFinal.versiones_anteriores || []).length);
}
print("listas_riesgo RSK-990 presente: " + (db.listas_riesgo.findOne({ _id: "RSK-990" }) !== null));
