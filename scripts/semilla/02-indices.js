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
// H-23: $graphLookup de la red de mulas une cada eslabon con el siguiente por
// connectToField "sinpe_origen", y restrictSearchWithMatch acota "fecha" a la
// ventana del episodio. El indice sirve las dos cosas en cada nivel.
db.transacciones.createIndex(
  { sinpe_origen: 1, fecha: 1 },
  { name: "idx_transacciones_sinpe_origen_fecha" }
);

// T-63: el pipeline de rasgos de grafo (D3) arranca con $match por rango de fecha. Sin
// este indice recorreria toda la coleccion en cada recalculo; con el, lee solo el
// horizonte (docs/06-plataforma.md, "R-11 y rasgos de grafo").
db.transacciones.createIndex(
  { fecha: 1 },
  { name: "idx_transacciones_fecha" }
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
// Consultas: el panel del agente filtra por estado y/o severidad y/o rango
// de fecha, y ordena por puntaje, fecha y _id (ORDEN_PANEL en
// app/repos/alertas.py, D-20: el puntaje da el orden de severidad); el
// motor busca por agente_id para la carga de trabajo; transaccion_id es la
// clave de idempotencia de todo el sistema (CLAUDE.md: "una transaccion
// produce una sola alerta"), por eso es unico.
// Regla igualdad, orden, rango (ESR): los campos de igualdad del filtro van
// primero y el orden del panel completo despues, asi el SORT sale del indice
// y no en memoria. Un indice por combinacion de filtros del panel. El rango
// de fecha_creacion va al final: se recorre el indice ya ordenado. Evidencia
// con explain en docs/09-explain-indices.md (H-06).
// ------------------------------------------------------------
db.alertas.createIndex(
  { transaccion_id: 1 },
  { name: "idx_alertas_transaccion_id", unique: true }
);
// idx_alertas_estado_severidad_fecha cambio de clave con D-20. createIndex
// con el mismo nombre y otra clave falla, asi que la clave vieja se borra
// antes: el script sigue siendo re-ejecutable sobre una base viva.
(function () {
  const clave = { estado: 1, severidad: 1, puntaje: -1, fecha_creacion: -1, _id: 1 };
  const previo = db.alertas.getIndexes().find(function (ix) {
    return ix.name === "idx_alertas_estado_severidad_fecha";
  });
  if (previo && JSON.stringify(previo.key) !== JSON.stringify(clave)) {
    db.alertas.dropIndex("idx_alertas_estado_severidad_fecha");
  }
  db.alertas.createIndex(clave, { name: "idx_alertas_estado_severidad_fecha" });
})();
db.alertas.createIndex(
  { estado: 1, puntaje: -1, fecha_creacion: -1, _id: 1 },
  { name: "idx_alertas_estado_puntaje_fecha" }
);
db.alertas.createIndex(
  { severidad: 1, puntaje: -1, fecha_creacion: -1, _id: 1 },
  { name: "idx_alertas_severidad_puntaje_fecha" }
);
db.alertas.createIndex(
  { puntaje: -1, fecha_creacion: -1, _id: 1 },
  { name: "idx_alertas_puntaje_fecha" }
);
db.alertas.createIndex(
  { agente_id: 1 },
  { name: "idx_alertas_agente" }
);
// T-82: las alertas a las que el cliente respondio "No fui yo" suben al tope de la cola. Indice
// PARCIAL (solo esas, un puñado) con el mismo orden del panel: la consulta de la cola las lee
// ya ordenadas, sin SORT en memoria, y el resto de la cola sigue con sus indices de siempre.
db.alertas.createIndex(
  { puntaje: -1, fecha_creacion: -1, _id: 1 },
  {
    name: "idx_alertas_no_fui_yo",
    partialFilterExpression: { "respuesta_cliente.valor": "no_fui_yo" }
  }
);
// Casos del panel (T-56): las alertas abiertas de UNA cuenta destino. El fragmento de un
// caso que pide el SSE por cada alerta nueva entra por aqui: igualdad de cuenta y de
// estado, sin recorrer las demas alertas abiertas.
db.alertas.createIndex(
  { cuenta_destino: 1, estado: 1 },
  { name: "idx_alertas_destino_estado" }
);
// Bitacora de auditoria (T-84): la consulta del administrador filtra las acciones del
// historial por fecha y por quien las hizo. Indices multillave, usados con $elemMatch para que
// la fecha y el agente se apliquen a la MISMA accion. Costo: dos entradas de indice por cada
// accion que se agrega al historial. Medido en docs/06-plataforma.md; si pesara, la bitacora
// funciona igual sin ellos (recorre `alertas`).
db.alertas.createIndex(
  { "historial.fecha": -1 },
  { name: "idx_alertas_historial_fecha" }
);
db.alertas.createIndex(
  { "historial.agente_id": 1, "historial.fecha": -1 },
  { name: "idx_alertas_historial_agente_fecha" }
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
// UNICO por (alerta_id, canal): es la clave de idempotencia de D2 (H-11). Una alerta
// CRITICA produce una sola notificacion por canal; reprocesar el evento tras una
// caida choca aca y no manda un segundo registro. El prefijo alerta_id sirve la
// consulta por alerta, asi que no hace falta un indice simple aparte.
// ------------------------------------------------------------
db.notificaciones.createIndex(
  { alerta_id: 1, canal: 1 },
  { name: "idx_notificaciones_alerta_canal", unique: true }
);
// T-82: el enlace del SMS se resuelve por el hash de su token. Unico y parcial (solo las
// notificaciones que lo traen): el correo no tiene enlace_hash.
db.notificaciones.createIndex(
  { enlace_hash: 1 },
  {
    name: "idx_notificaciones_enlace_hash",
    unique: true,
    partialFilterExpression: { enlace_hash: { $exists: true } }
  }
);
// Consulta de la lista del supervisor: las mas recientes primero.
db.notificaciones.createIndex(
  { fecha: -1 },
  { name: "idx_notificaciones_fecha" }
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
