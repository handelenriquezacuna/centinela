// ============================================================
// Centinela — semilla 02: indices con nombre semantico
// ============================================================
//
// Se corre despues de 01-colecciones.js:
//
//   mongosh --file scripts/semilla/01-colecciones.js
//   mongosh --file scripts/semilla/02-indices.js
//
// Idempotente: createIndex con la misma definicion y el mismo nombre no
// hace nada en una segunda corrida (MongoDB lo detecta como ya existente).
//
// Cada indice esta pensado desde una consulta real del panel o del motor
// (docs/02-casos-de-uso.md), no "por si acaso". El nombre lleva el prefijo
// de la coleccion y describe los campos en el orden en que entran al
// indice, ej. idx_alertas_estado_severidad_fecha.
//
// Donde una coleccion no tiene indice adicional (perfiles_comportamiento,
// indicadores_diarios) es porque su unica clave de acceso es _id, que ya
// tiene indice por defecto — crear otro seria redundante ("por si acaso").

db = db.getSiblingDB("antifraude");

print("==================================================");
print(" Centinela - semilla 02: indices");
print("==================================================");

// ------------------------------------------------------------
// clientes
// Consulta: ubicar al cliente por cedula (atencion al cliente, UC-03).
// ------------------------------------------------------------
db.clientes.createIndex(
  { cedula: 1 },
  { name: "idx_clientes_cedula", unique: true }
);

// ------------------------------------------------------------
// cuentas
// Consultas: cuentas de un cliente (UC-03: abrir la alerta y ver el
// perfil), y resolver una cuenta por su numero SINPE (busqueda inversa).
// ------------------------------------------------------------
db.cuentas.createIndex(
  { cliente_id: 1 },
  { name: "idx_cuentas_cliente" }
);
db.cuentas.createIndex(
  { cuenta_sinpe: 1 },
  { name: "idx_cuentas_sinpe", unique: true }
);

// perfiles_comportamiento: sin indice adicional, ver nota de cabecera
// (_id ya es el cliente_id).

// ------------------------------------------------------------
// reglas_deteccion
// Consultas: resolver una regla por su codigo corto (R-01) y el motor
// cargando solo las reglas activas antes de evaluar (UC-02/H-08).
// ------------------------------------------------------------
db.reglas_deteccion.createIndex(
  { codigo: 1 },
  { name: "idx_reglas_codigo", unique: true }
);
db.reglas_deteccion.createIndex(
  { activa: 1 },
  { name: "idx_reglas_activa" }
);

// ------------------------------------------------------------
// listas_riesgo
// Consulta: el evaluador de reglas pregunta si una cuenta destino esta en
// la lista de riesgo (regla R-07) en cada transaccion; tiene que ser rapida.
// ------------------------------------------------------------
db.listas_riesgo.createIndex(
  { cuenta: 1 },
  { name: "idx_listas_riesgo_cuenta", unique: true }
);

// ------------------------------------------------------------
// transacciones
// Consultas: UC-05 busqueda forense por cuenta_origen + rango de fecha
// (el ejemplo del caso de uso es literalmente "cuenta_origen + timestamp");
// UC-04 sigue el dinero desde cuenta_destino ($graphLookup); UC-05 tambien
// filtra por monto sobre un umbral dentro de un rango de fechas.
// ------------------------------------------------------------
db.transacciones.createIndex(
  { cuenta_origen: 1, fecha: -1 },
  { name: "idx_transacciones_cuenta_origen_fecha" }
);
db.transacciones.createIndex(
  { cuenta_destino: 1 },
  { name: "idx_transacciones_cuenta_destino" }
);
db.transacciones.createIndex(
  { monto_crc: -1, fecha: -1 },
  { name: "idx_transacciones_monto_fecha" }
);

// ------------------------------------------------------------
// agentes
// Consulta: filtrar agentes vs supervisores/administradores.
// ------------------------------------------------------------
db.agentes.createIndex(
  { rol: 1 },
  { name: "idx_agentes_rol" }
);

// ------------------------------------------------------------
// alertas
// Consultas: el panel del agente filtra por estado y ordena por severidad
// y fecha (UC-03/UC-06); el motor busca por agente_id para la carga de
// trabajo; transaccion_id es la clave de idempotencia de todo el sistema
// (CLAUDE.md: "una transaccion produce una sola alerta"), por eso es unico.
// ------------------------------------------------------------
db.alertas.createIndex(
  { transaccion_id: 1 },
  { name: "idx_alertas_transaccion_id", unique: true }
);
db.alertas.createIndex(
  { estado: 1, severidad: -1, fecha_creacion: -1 },
  { name: "idx_alertas_estado_severidad_fecha" }
);
db.alertas.createIndex(
  { agente_id: 1 },
  { name: "idx_alertas_agente" }
);

// ------------------------------------------------------------
// casos
// Consulta: listar casos abiertos en investigacion (UC-04).
// ------------------------------------------------------------
db.casos.createIndex(
  { estado: 1 },
  { name: "idx_casos_estado" }
);

// ------------------------------------------------------------
// notificaciones
// Consulta: historial de notificaciones de una alerta (UC-08).
// ------------------------------------------------------------
db.notificaciones.createIndex(
  { alerta_id: 1 },
  { name: "idx_notificaciones_alerta" }
);

// indicadores_diarios: sin indice adicional, ver nota de cabecera
// (_id ya es la fecha).

// ------------------------------------------------------------
// politica_deteccion (coleccion de operacion, no una de las 11 de
// dominio; ver el bloque 12 de 01-colecciones.js). Garantiza una sola
// politica vigente a la vez: indice unico parcial sobre vigente:true.
// ------------------------------------------------------------
db.politica_deteccion.createIndex(
  { vigente: 1 },
  { unique: true, partialFilterExpression: { vigente: true }, name: "idx_politica_vigente" }
);

// ------------------------------------------------------------
// Verificacion: nombre de los indices por coleccion
// ------------------------------------------------------------
print("--------------------------------------------------");
[
  "clientes", "cuentas", "perfiles_comportamiento", "reglas_deteccion",
  "listas_riesgo", "transacciones", "agentes", "alertas", "casos",
  "notificaciones", "indicadores_diarios", "politica_deteccion"
].forEach(function (nombre) {
  const nombres = db.getCollection(nombre).getIndexes().map(function (ix) { return ix.name; });
  print(nombre + ": " + nombres.join(", "));
});
print("==================================================");
print(" 02-indices.js terminado");
print("==================================================");
