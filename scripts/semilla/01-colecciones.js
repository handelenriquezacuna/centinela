// ============================================================
// Centinela — semilla 01: colecciones de dominio y validadores
// ============================================================
//
// Fuente de verdad de las 11 colecciones de dominio (Practica 1, ahora a
// escala de proyecto). Se corre con:
//
//   mongosh --file scripts/semilla/01-colecciones.js
//
// Idempotente: cada una de las 11 colecciones de dominio se recrea (drop +
// createCollection) en cada corrida, asi que correrlo dos veces seguidas
// deja el mismo resultado. No dropea la base completa para no arrastrar
// `deteccion_estado` (coleccion de operacion que no toca este archivo) ni
// las vistas de H-24.
//
// *** ORDEN OBLIGATORIO: 01 -> 02 -> 03 ***
// `01-colecciones.js` (este archivo) SIEMPRE antes que
// `scripts/semilla/03-catalogos.js` (H-07). La coleccion `reglas_deteccion`
// se recrea con drop+create cada vez que corre este archivo: si `01` se
// corre DESPUES de `03`, borra el catalogo real de 6 reglas (REG-001..006)
// que sembro H-07 y lo deja vacio otra vez. Estado esperado de ese catalogo
// (pedido a H-07, no confirmado poblado al momento de escribir esto): cada
// regla con `version` y, donde aplique, `parametros`.
// `politica_deteccion` (ver el bloque 12, mas abajo) SI es segura de
// re-correr en cualquier orden: usa `asegurarValidador()` (crea o hace
// `collMod`, nunca dropea), porque ya trae un documento real de H-07 que
// no es responsabilidad de este archivo reponer.
//
// Contrato de datos (ver CLAUDE.md y scripts/practica1/CONTRATO-IDS.md):
//   - Base de datos: "antifraude" (no "centinela").
//   - Dinero: enteros de 64 bits en colones -> NumberLong(...) +
//     bsonType "long". Nunca double. Campo moneda: "CRC" en transacciones.
//   - Fechas: ISODate en UTC (BSON Date siempre es UTC internamente; se
//     escribe con el offset local -06:00 de Costa Rica para que se lea
//     igual que el caso de uso, Mongo lo normaliza al guardarlo).
//     Unica excepcion: indicadores_diarios.fecha, texto "YYYY-MM-DD".
//   - Identificadores: cadenas legibles del contrato (CLI-001, TXN-001),
//     nunca ObjectId. Dos casos que el contrato no cubria y quedan
//     cerrados aqui: perfiles_comportamiento._id = cliente_id, y
//     indicadores_diarios._id = la fecha "YYYY-MM-DD".
//   - esquema_version: entero, obligatorio desde el primer documento, en
//     las 11 colecciones.
//   - snake_case en espanol. Prohibido "id", "type", "class" como campo.
//   - alertas.reglas_disparadas es arreglo de cadenas por ahora (v1). En
//     H-08 evoluciona a copia embebida con version (esquema_version 1->2,
//     migracion de H-26). No se adelanta esa forma aqui.
//   - Maquina de estados de alertas.estado: nueva -> en_revision ->
//     confirmada / descartada / escalada. El validador la restringe.
//
// Datos de ejemplo: el caso de uso ya documentado en docs/02-casos-de-uso.md
// y scripts/practica1/CONTRATO-IDS.md (Maria Rodriguez, cuenta 8712-4455,
// TXN-001 de 750000 a las 22:47, agente Jose Solis). No se inventan clientes
// ni montos nuevos; donde el contrato no da un valor (p.ej. destinos
// frecuentes del perfil, fechas de alta de los otros 3 clientes) se marca
// con el comentario "dato de relleno" y un valor plausible.
//
// Fuera de alcance de este archivo (H-01/H-02): el generador Faker
// (H-03/H-04/H-05) y las vistas (H-24). reglas_deteccion SI se crea aqui
// (es una de las 11 de dominio, viene desde Practica 1) pero se deja sin
// documentos: poblarla con el catalogo real es H-07, en
// scripts/semilla/03-catalogos.js. politica_deteccion NO es una de las 11
// de dominio (es 1 de las 2 colecciones de operacion de CLAUDE.md), pero
// su validador SI se agrega aqui (bloque 12) a pedido de H-07, que la creo
// sin esquema; el documento real lo puebla H-07, no este archivo.

db = db.getSiblingDB("antifraude");

function recrearColeccion(nombre, validator) {
  if (db.getCollectionNames().indexOf(nombre) !== -1) {
    db.getCollection(nombre).drop();
  }
  db.createCollection(nombre, {
    validator: { $jsonSchema: validator },
    validationLevel: "strict",
    validationAction: "error"
  });
}

// Para colecciones que otra historia puede haber poblado ya (hoy:
// politica_deteccion, de H-07): nunca se dropea. Si no existe, se crea con
// el validador; si ya existe, se le aplica el validador con `collMod` sin
// tocar los documentos que ya tiene.
function asegurarValidador(nombre, validator) {
  const opciones = { validator: { $jsonSchema: validator }, validationLevel: "strict", validationAction: "error" };
  if (db.getCollectionNames().indexOf(nombre) === -1) {
    db.createCollection(nombre, opciones);
  } else {
    db.runCommand(Object.assign({ collMod: nombre }, opciones));
  }
}

print("==================================================");
print(" Centinela - semilla 01: colecciones y validadores");
print(" Base: antifraude");
print("==================================================");

// ------------------------------------------------------------
// 1. CLIENTES
// Proposito: identidad de la victima potencial (cliente del banco).
// ------------------------------------------------------------
recrearColeccion("clientes", {
  bsonType: "object",
  title: "clientes",
  required: ["_id", "nombre", "cedula", "fecha_registro", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^CLI-[0-9]{3}$",
      description: "Identificador legible del contrato de IDs, ej. CLI-001"
    },
    nombre: {
      bsonType: "string",
      minLength: 1,
      description: "Nombre completo del cliente"
    },
    cedula: {
      bsonType: "string",
      pattern: "^[0-9]-[0-9]{4}-[0-9]{4}$",
      description: "Cedula de identidad, formato 1-1111-1111"
    },
    fecha_registro: {
      bsonType: "date",
      description: "ISODate UTC de alta del cliente"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

// Documento de ejemplo real: Maria Rodriguez (CONTRATO-IDS.md). Los otros
// 3 clientes son el relleno del contrato (Luis, Ana, Carlos); sus fechas de
// registro no estan dadas en ninguna fuente, son dato de relleno.
db.clientes.insertMany([
  { _id: "CLI-001", nombre: "María Rodríguez Vargas", cedula: "1-1111-1111", fecha_registro: new Date("2025-01-15T00:00:00-06:00"), esquema_version: 1 },
  { _id: "CLI-002", nombre: "Luis Fernández Solano", cedula: "2-2222-2222", fecha_registro: new Date("2024-11-02T00:00:00-06:00"), esquema_version: 1 },
  { _id: "CLI-003", nombre: "Ana Castro Jiménez", cedula: "3-3333-3333", fecha_registro: new Date("2025-03-10T00:00:00-06:00"), esquema_version: 1 },
  { _id: "CLI-004", nombre: "Carlos Mora Rojas", cedula: "4-4444-4444", fecha_registro: new Date("2025-06-20T00:00:00-06:00"), esquema_version: 1 }
]);

// ------------------------------------------------------------
// 2. CUENTAS
// Proposito: cuenta SINPE de un cliente nuestro (las cuentas mula NO
// tienen documento aqui, son solo un numero dentro de transacciones y,
// si se detectan, un documento en listas_riesgo).
// Relacion cuentas -> clientes: REFERENCIADA (cliente_id), no embebida.
// Justificacion: una cuenta puede cambiar de titular; el cliente no debe
// cargar sus cuentas embebidas porque la relacion es la que cambia, no el
// cliente.
// ------------------------------------------------------------
recrearColeccion("cuentas", {
  bsonType: "object",
  title: "cuentas",
  required: ["_id", "cuenta_sinpe", "cliente_id", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^CTA-[0-9]{3}$",
      description: "Identificador legible del contrato de IDs, ej. CTA-001"
    },
    cuenta_sinpe: {
      bsonType: "string",
      pattern: "^[0-9]{4}-[0-9]{4}$",
      description: "Numero SINPE Movil de la cuenta, formato 8712-4455"
    },
    cliente_id: {
      bsonType: "string",
      pattern: "^CLI-[0-9]{3}$",
      description: "Referencia a clientes._id (titular actual)"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

db.cuentas.insertMany([
  { _id: "CTA-001", cuenta_sinpe: "8712-4455", cliente_id: "CLI-001", esquema_version: 1 },
  { _id: "CTA-002", cuenta_sinpe: "6210-3387", cliente_id: "CLI-002", esquema_version: 1 },
  { _id: "CTA-003", cuenta_sinpe: "5544-9021", cliente_id: "CLI-003", esquema_version: 1 },
  { _id: "CTA-004", cuenta_sinpe: "3390-6612", cliente_id: "CLI-004", esquema_version: 1 }
]);

// ------------------------------------------------------------
// 3. PERFILES_COMPORTAMIENTO
// Proposito: resumen materializado del comportamiento habitual de un
// cliente (lo carga el motor en cada transaccion para comparar). En el
// proyecto real lo produce un pipeline $merge (H-21); aqui se inserta a
// mano el de Maria, que es el unico documentado con valores reales.
// Relacion con clientes: es 1 a 1 y se resuelve con la propia _id, por
// eso _id = cliente_id (decision de contrato, no la cubria CONTRATO-IDS.md
// y el scaffold de Practica 1 dejaba que Mongo generara un ObjectId, lo
// cual violaba el contrato de identificadores). No se guarda un campo
// cliente_id aparte porque seria un duplicado exacto de _id.
// ------------------------------------------------------------
recrearColeccion("perfiles_comportamiento", {
  bsonType: "object",
  title: "perfiles_comportamiento",
  required: ["_id", "promedio_crc", "maximo_crc", "num_transferencias", "horario_habitual", "destinos_frecuentes", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^CLI-[0-9]{3}$",
      description: "Igual al cliente_id: un perfil materializado por cliente"
    },
    promedio_crc: {
      bsonType: "long",
      minimum: 0,
      description: "Monto promedio de transferencia en colones enteros"
    },
    maximo_crc: {
      bsonType: "long",
      minimum: 0,
      description: "Monto maximo historico en colones enteros"
    },
    desviacion_crc: {
      bsonType: "long",
      minimum: 0,
      description: "Desviacion estandar del monto en colones enteros (H-21: 'calcula promedio, desviacion, maximo...'). Opcional: CONTRATO-IDS.md no da un valor real para CLI-001, se completa cuando el pipeline $merge de H-21 la calcule"
    },
    num_transferencias: {
      bsonType: "int",
      minimum: 0,
      description: "Cantidad de transferencias consideradas en la ventana de calculo"
    },
    horario_habitual: {
      bsonType: "object",
      required: ["inicio", "fin"],
      additionalProperties: false,
      properties: {
        inicio: { bsonType: "string", pattern: "^([01][0-9]|2[0-3]):[0-5][0-9]$", description: "Hora local de inicio, HH:MM" },
        fin: { bsonType: "string", pattern: "^([01][0-9]|2[0-3]):[0-5][0-9]$", description: "Hora local de fin, HH:MM" }
      },
      description: "Ventana horaria habitual del cliente, hora de Costa Rica"
    },
    destinos_frecuentes: {
      bsonType: "array",
      minItems: 1,
      items: { bsonType: "string", pattern: "^[0-9]{4}-[0-9]{4}$" },
      description: "Numeros SINPE que el cliente usa con regularidad"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

// Valores reales documentados para CLI-001 (docs/02-casos-de-uso.md y
// CONTRATO-IDS.md): 47 transferencias, promedio 45200, maximo 180000,
// horario 07:00-19:00. destinos_frecuentes no tiene numeros dados en
// ninguna fuente (solo se dice "5 numeros, todos con 3+ transferencias
// previas"): son dato de relleno, 3 numeros para no inventar de mas.
// Solo se puebla el perfil de Maria: los de CLI-002..004 los produce el
// generador de H-03 con datos sinteticos, no se inventan aqui a mano.
db.perfiles_comportamiento.insertMany([
  {
    _id: "CLI-001",
    promedio_crc: NumberLong("45200"),
    maximo_crc: NumberLong("180000"),
    num_transferencias: 47,
    horario_habitual: { inicio: "07:00", fin: "19:00" },
    destinos_frecuentes: ["2233-4455", "3344-5566", "4455-6677"],
    esquema_version: 1
  }
]);

// ------------------------------------------------------------
// 4. REGLAS_DETECCION
// Proposito: catalogo de reglas de deteccion vivas en base de datos (para
// poder ajustar peso/umbral sin desplegar codigo, ver UC-07).
// Se crea la coleccion y su validador aqui porque es una de las 11 de
// dominio (Practica 1), pero a proposito NO se puebla con documentos: el
// catalogo real (6 reglas REG-001..006, con codigo, tipo, umbral, peso,
// version y estado tuneados) es H-07, en scripts/semilla/03-catalogos.js.
// Insertar aqui los 3 codigos de relleno (REG-001..003) hubiera sido pisar
// ese trabajo con datos a medio madurar.
// version y parametros se agregaron a pedido de H-07 (divergencia real
// contra el modelo Pydantic `Regla` del arquitecto):
//   - version: la alerta debe conservar con que version de la regla se
//     evaluo (H-16); sin este campo la primera carga de las 6 reglas
//     reales fallaba el validador y quedo guardada sin version.
//   - parametros: `umbral` es un solo numero, pero varias reglas tienen
//     una perilla secundaria (R-09 cuenta transferencias EN una ventana de
//     minutos, R-03 mira una ventana de dias de historial, R-11 usa
//     ventana de minutos y saltos). Sin un lugar generico para guardarlas,
//     H-08 las hubiera tenido que codificar en Python, rompiendo la regla
//     de oro de que las reglas viven en datos. Es un objeto generico por
//     tipo, no un campo por familia de regla, para no acoplar el esquema a
//     cuantos tipos de regla existan hoy.
// ------------------------------------------------------------
recrearColeccion("reglas_deteccion", {
  bsonType: "object",
  title: "reglas_deteccion",
  required: ["_id", "codigo", "descripcion", "tipo", "umbral", "peso", "activa", "version", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^REG-[0-9]{3}$",
      description: "Identificador legible del contrato de IDs, ej. REG-001"
    },
    codigo: {
      bsonType: "string",
      pattern: "^R-[0-9]{2}$",
      description: "Codigo corto tal como aparece en los casos de uso, ej. R-01"
    },
    descripcion: {
      bsonType: "string",
      minLength: 1,
      description: "Que evalua la regla, en lenguaje de negocio"
    },
    tipo: {
      bsonType: "string",
      minLength: 1,
      description: "Categoria de la regla (monto, destino, horario, lista_riesgo, velocidad...); el catalogo final lo define H-07"
    },
    umbral: {
      bsonType: ["int", "long", "double"],
      minimum: 0,
      description: "Valor de corte principal de la regla; su unidad depende del tipo (multiplo, colones, minutos...)"
    },
    parametros: {
      bsonType: "object",
      description: "Perillas secundarias de la regla, por tipo (p.ej. ventana_minutos, dias_historial, saltos). Evita constantes en el codigo. Opcional"
    },
    peso: {
      bsonType: "int",
      minimum: 0,
      description: "Puntos que aporta al puntaje de la alerta si dispara"
    },
    activa: {
      bsonType: "bool",
      description: "Si la regla participa en la evaluacion (UC-07: se puede activar/desactivar)"
    },
    version: {
      bsonType: "int",
      minimum: 1,
      description: "Version de la regla; la alerta guarda con cual se evaluo (H-16)"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

// Sin insertMany a proposito: ver nota arriba. db.reglas_deteccion.countDocuments({})
// va a dar 0 hasta que corra 03-catalogos.js (H-07).

// ------------------------------------------------------------
// 5. LISTAS_RIESGO
// Proposito: cuentas mula conocidas (UC-09); alimentan la regla de
// destino en lista de riesgo. Es la cadena de mulas de UC-04.
// ------------------------------------------------------------
recrearColeccion("listas_riesgo", {
  bsonType: "object",
  title: "listas_riesgo",
  required: ["_id", "cuenta", "motivo", "fecha_reporte", "vigente", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^RSK-[0-9]{3}$",
      description: "Identificador legible del contrato de IDs, ej. RSK-001"
    },
    cuenta: {
      bsonType: "string",
      pattern: "^[0-9]{4}-[0-9]{4}$",
      description: "Numero SINPE de la cuenta senalada como mula"
    },
    motivo: {
      bsonType: "string",
      minLength: 1,
      description: "Por que se marco la cuenta como riesgo"
    },
    fecha_reporte: {
      bsonType: "date",
      description: "ISODate UTC de cuando se reporto la cuenta"
    },
    vigente: {
      bsonType: "bool",
      description: "Si el reporte sigue activo (UC-09: la entrada tiene vigencia)"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

// fecha_reporte no esta dada en el contrato (solo cuenta y motivo): se usa
// el dia siguiente al caso UC-02 como dato de relleno razonable (cuando el
// agente investiga la cadena en UC-04).
db.listas_riesgo.insertMany([
  { _id: "RSK-001", cuenta: "6033-9001", motivo: "Primer salto de una cadena de fragmentacion", fecha_reporte: new Date("2026-09-25T09:00:00-06:00"), vigente: true, esquema_version: 1 },
  { _id: "RSK-002", cuenta: "7104-2288", motivo: "Segundo salto", fecha_reporte: new Date("2026-09-25T09:00:00-06:00"), vigente: true, esquema_version: 1 },
  { _id: "RSK-003", cuenta: "8455-1177", motivo: "Tercer salto", fecha_reporte: new Date("2026-09-25T09:00:00-06:00"), vigente: true, esquema_version: 1 },
  { _id: "RSK-004", cuenta: "6001-3344", motivo: "Cuarto salto", fecha_reporte: new Date("2026-09-25T09:00:00-06:00"), vigente: true, esquema_version: 1 }
]);

// ------------------------------------------------------------
// 6. TRANSACCIONES
// Proposito: cada transferencia SINPE Movil, la que ve el motor por
// Change Stream (UC-01/UC-02). cuenta_destino es un numero de cuenta
// plano (no referencia): el destino puede no ser cliente nuestro.
// monto_crc es la trampa del contrato: entero de 64 bits, nunca double.
// ------------------------------------------------------------
recrearColeccion("transacciones", {
  bsonType: "object",
  title: "transacciones",
  required: ["_id", "cuenta_origen", "cuenta_destino", "monto_crc", "moneda", "canal", "fecha", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^TXN-[0-9]{3,}$",
      description: "Identificador legible del contrato de IDs, ej. TXN-001"
    },
    cuenta_origen: {
      bsonType: "string",
      pattern: "^CTA-[0-9]{3}$",
      description: "Referencia a cuentas._id, la cuenta que envia"
    },
    cuenta_destino: {
      bsonType: "string",
      pattern: "^[0-9]{4}-[0-9]{4}$",
      description: "Numero SINPE destino; puede ser una cuenta mula sin documento propio"
    },
    monto_crc: {
      bsonType: "long",
      minimum: 1,
      description: "Monto en colones enteros. Entero de 64 bits, NUNCA double"
    },
    moneda: {
      enum: ["CRC"],
      description: "Fija en CRC; SINPE Movil no opera otra moneda"
    },
    canal: {
      enum: ["sinpe_movil"],
      description: "Unico canal que cubre el sistema por ahora"
    },
    fecha: {
      bsonType: "date",
      description: "ISODate UTC del momento de la transferencia"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

// TXN-001 es la transaccion sospechosa de UC-02/UC-03, con los valores
// EXACTOS del caso de uso. TXN-002..004 son el relleno de transacciones
// normales (monto y horario de oficina) que pide el contrato para que se
// note el contraste; sus montos y destinos no estan dados en ninguna
// fuente, son dato de relleno.
db.transacciones.insertMany([
  { _id: "TXN-001", cuenta_origen: "CTA-001", cuenta_destino: "6033-9001", monto_crc: NumberLong("750000"), moneda: "CRC", canal: "sinpe_movil", fecha: new Date("2026-09-24T22:47:00-06:00"), esquema_version: 1 },
  { _id: "TXN-002", cuenta_origen: "CTA-002", cuenta_destino: "2233-4455", monto_crc: NumberLong("15000"), moneda: "CRC", canal: "sinpe_movil", fecha: new Date("2026-09-24T10:15:00-06:00"), esquema_version: 1 },
  { _id: "TXN-003", cuenta_origen: "CTA-003", cuenta_destino: "3344-5566", monto_crc: NumberLong("22000"), moneda: "CRC", canal: "sinpe_movil", fecha: new Date("2026-09-24T11:40:00-06:00"), esquema_version: 1 },
  { _id: "TXN-004", cuenta_origen: "CTA-004", cuenta_destino: "4455-6677", monto_crc: NumberLong("8000"), moneda: "CRC", canal: "sinpe_movil", fecha: new Date("2026-09-24T14:05:00-06:00"), esquema_version: 1 }
]);

// ------------------------------------------------------------
// 7. AGENTES
// Proposito: personal que trabaja alertas (agente de fraude, supervisor,
// administrador). rol enum se deja abierto a los 3 roles que aparecen en
// docs/02-casos-de-uso.md aunque los 2 documentos de ejemplo sean agentes.
// ------------------------------------------------------------
recrearColeccion("agentes", {
  bsonType: "object",
  title: "agentes",
  required: ["_id", "nombre", "rol", "activo", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^AGT-[0-9]{3}$",
      description: "Identificador legible del contrato de IDs, ej. AGT-001"
    },
    nombre: {
      bsonType: "string",
      minLength: 1,
      description: "Nombre completo del agente"
    },
    rol: {
      enum: ["agente", "supervisor", "administrador"],
      description: "Rol del actor dentro del sistema (docs/02-casos-de-uso.md)"
    },
    activo: {
      bsonType: "bool",
      description: "Si la cuenta del agente esta habilitada"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

db.agentes.insertMany([
  { _id: "AGT-001", nombre: "José Solís", rol: "agente", activo: true, esquema_version: 1 },
  { _id: "AGT-002", nombre: "Laura Méndez", rol: "agente", activo: true, esquema_version: 1 }
]);

// ------------------------------------------------------------
// 8. ALERTAS
// Proposito: la coleccion mas importante del proyecto. La crea el motor
// de deteccion (Change Stream D1, UC-02) y el agente la resuelve (UC-03).
// Relacion alertas -> agente: REFERENCIADA (agente_id). Justificacion:
// una alerta cambia de dueno con frecuencia (reasignacion); duplicar los
// datos del agente en cada alerta seria incoherente apenas cambie.
// Relacion alertas -> historial: EMBEBIDA. Justificacion: el historial de
// acciones se lee siempre junto con la alerta, nunca solo, y crece acotado
// (pocas acciones por alerta) -- exactamente el criterio de docs/04.
// reglas_disparadas: arreglo de cadenas (v1, esquema_version=1). H-08 lo
// evoluciona a copia embebida con version de la regla (esquema_version 2,
// migracion de H-26); no se adelanta esa forma en este archivo.
// agente_id NO es obligatorio: una alerta "nueva" puede no tener agente
// asignado todavia.
// cuenta_origen, cuenta_destino, monto_crc, moneda y cliente_nombre son
// copia DESNORMALIZADA de la transaccion y el cliente al momento en que se
// crea la alerta (decision de H-17: la fila del panel los necesita sin
// abrir el detalle, y "nada se recalcula dentro de una peticion HTTP" —
// docs/04-arquitectura.md — asi que no se hace $lookup en cada pagina).
// Mismo principio que reglas_disparadas: la alerta se explica sola dentro
// de un año, aunque la cuenta cambie de titular o el cliente se renombre.
// Ojo con la semantica: aqui cuenta_origen/cuenta_destino son el NUMERO
// SINPE (formato 8712-4455) para mostrarlo tal cual en el panel, no el
// _id de cuentas (CTA-001) que usa transacciones.cuenta_origen — son
// colecciones distintas con el mismo nombre de campo y distinto contenido
// a proposito (una es referencia interna, la otra es copia de display).
// ------------------------------------------------------------
recrearColeccion("alertas", {
  bsonType: "object",
  title: "alertas",
  required: ["_id", "transaccion_id", "reglas_disparadas", "puntaje", "severidad", "estado", "fecha_creacion", "historial", "esquema_version", "cuenta_origen", "cuenta_destino", "monto_crc", "moneda", "cliente_nombre"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^ALR-[0-9]{3}$",
      description: "Identificador legible del contrato de IDs, ej. ALR-001"
    },
    transaccion_id: {
      bsonType: "string",
      pattern: "^TXN-[0-9]{3,}$",
      description: "Referencia a transacciones._id; clave de idempotencia (indice unico en 02-indices.js): una transaccion produce una sola alerta"
    },
    cuenta_origen: {
      bsonType: "string",
      pattern: "^[0-9]{4}-[0-9]{4}$",
      description: "Copia desnormalizada del numero SINPE de origen, para la fila del panel sin $lookup"
    },
    cuenta_destino: {
      bsonType: "string",
      pattern: "^[0-9]{4}-[0-9]{4}$",
      description: "Copia desnormalizada del numero SINPE de destino, para la fila del panel sin $lookup"
    },
    monto_crc: {
      bsonType: "long",
      minimum: 1,
      description: "Copia desnormalizada del monto en colones enteros. Entero de 64 bits, NUNCA double"
    },
    moneda: {
      enum: ["CRC"],
      description: "Copia desnormalizada de la moneda de la transaccion"
    },
    cliente_nombre: {
      bsonType: "string",
      minLength: 1,
      description: "Copia desnormalizada del nombre del cliente, para la fila del panel sin $lookup"
    },
    reglas_disparadas: {
      bsonType: "array",
      minItems: 1,
      uniqueItems: true,
      items: { bsonType: "string", pattern: "^REG-[0-9]{3}$" },
      description: "v1: arreglo de codigos de reglas_deteccion. Evoluciona a copia embebida con version en H-08/H-26 (esquema_version 2)"
    },
    puntaje: {
      bsonType: "int",
      minimum: 0,
      maximum: 100,
      description: "Suma de pesos de las reglas que dispararon, recortada a 100 (politica_deteccion.puntaje_maximo); coincide con el limite `le=100` del modelo Pydantic. Los pesos de las 5 reglas ACTIVAS de H-07 suman 145 (el maximo alcanzable hoy); las 6 reglas del catalogo (incluida REG-006, que nace apagada) suman 165. De cualquiera de las dos formas el evaluador SI puede producir mas de 100 antes de recortar"
    },
    severidad: {
      enum: ["MEDIA", "ALTA", "CRITICA"],
      description: "No existe severidad BAJA: si el puntaje no supera el umbral, UC-02 dice que no se crea alerta"
    },
    estado: {
      enum: ["nueva", "en_revision", "confirmada", "descartada", "escalada"],
      description: "Maquina de estados de la alerta: nueva -> en_revision -> confirmada/descartada/escalada"
    },
    agente_id: {
      bsonType: "string",
      pattern: "^AGT-[0-9]{3}$",
      description: "Referencia a agentes._id; ausente mientras la alerta este sin asignar"
    },
    fecha_creacion: {
      bsonType: "date",
      description: "ISODate UTC de cuando el motor creo la alerta"
    },
    historial: {
      bsonType: "array",
      minItems: 1,
      description: "Bitacora embebida de acciones sobre la alerta (UC-03)",
      items: {
        bsonType: "object",
        required: ["accion", "fecha"],
        additionalProperties: false,
        properties: {
          accion: {
            enum: ["nueva", "en_revision", "confirmada", "descartada", "escalada"],
            description: "Estado al que quedo la alerta tras esta accion"
          },
          agente_id: {
            bsonType: ["string", "null"],
            description: "Quien hizo la accion; null si fue automatica (creacion por el motor)"
          },
          fecha: {
            bsonType: "date",
            description: "ISODate UTC de la accion"
          },
          nota: {
            bsonType: "string",
            description: "Nota libre del agente (opcional)"
          }
        }
      }
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento (1 = reglas_disparadas como arreglo de cadenas)"
    }
  }
});

// ALR-001 es exactamente UC-02/UC-03: puntaje 75, severidad CRITICA, las 3
// reglas de relleno del contrato, resuelta por AGT-001 (Jose Solis) como
// "confirmada" con la nota del caso de uso. Los campos desnormalizados
// (cuenta_origen, cuenta_destino, monto_crc, moneda, cliente_nombre) son
// la copia exacta de TXN-001 y de Maria Rodriguez Vargas (CLI-001) al
// momento de crear la alerta.
db.alertas.insertMany([
  {
    _id: "ALR-001",
    transaccion_id: "TXN-001",
    cuenta_origen: "8712-4455",
    cuenta_destino: "6033-9001",
    monto_crc: NumberLong("750000"),
    moneda: "CRC",
    cliente_nombre: "María Rodríguez Vargas",
    reglas_disparadas: ["REG-001", "REG-002", "REG-003"],
    puntaje: 75,
    severidad: "CRITICA",
    estado: "confirmada",
    agente_id: "AGT-001",
    fecha_creacion: new Date("2026-09-24T22:47:05-06:00"),
    historial: [
      { accion: "confirmada", agente_id: "AGT-001", fecha: new Date("2026-09-24T22:53:00-06:00"), nota: "Cliente confirma llamada previa" }
    ],
    esquema_version: 1
  }
]);

// ------------------------------------------------------------
// 9. CASOS
// Proposito: escalacion de una alerta confirmada para investigar (UC-04,
// la cadena de mulas). alertas es un arreglo porque un caso puede agrupar
// mas de una alerta relacionada (misma red).
// ------------------------------------------------------------
recrearColeccion("casos", {
  bsonType: "object",
  title: "casos",
  required: ["_id", "alertas", "estado", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^CAS-[0-9]{3}$",
      description: "Identificador legible del contrato de IDs, ej. CAS-001"
    },
    alertas: {
      bsonType: "array",
      minItems: 1,
      uniqueItems: true,
      items: { bsonType: "string", pattern: "^ALR-[0-9]{3}$" },
      description: "Referencias a alertas._id agrupadas en este caso"
    },
    estado: {
      enum: ["investigacion", "cerrado"],
      description: "Estado del caso; el contrato solo documenta 'investigacion' (UC-04)"
    },
    fecha_apertura: {
      bsonType: "date",
      description: "ISODate UTC de cuando se abrio el caso. Opcional: el modelo Pydantic la declara, CONTRATO-IDS.md no da un valor real para CAS-001"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

db.casos.insertMany([
  { _id: "CAS-001", alertas: ["ALR-001"], estado: "investigacion", esquema_version: 1 }
]);

// ------------------------------------------------------------
// 10. NOTIFICACIONES
// Proposito: registro de envio del correo de riesgo critico (UC-08, motor
// D2). Es la bitacora de que se avisó, no el contenido del correo.
// ------------------------------------------------------------
recrearColeccion("notificaciones", {
  bsonType: "object",
  title: "notificaciones",
  required: ["_id", "alerta_id", "canal", "estado_envio", "fecha", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^NOT-[0-9]{3}$",
      description: "Identificador legible del contrato de IDs, ej. NOT-001"
    },
    alerta_id: {
      bsonType: "string",
      pattern: "^ALR-[0-9]{3}$",
      description: "Referencia a alertas._id que origino la notificacion"
    },
    canal: {
      enum: ["correo", "sms"],
      description: "UC-08 solo documenta correo; sms se deja abierto para el canal de guardia"
    },
    estado_envio: {
      enum: ["pendiente", "enviado", "fallido"],
      description: "UC-08: fallo de SMTP se registra como fallido y se reintenta"
    },
    fecha: {
      bsonType: "date",
      description: "ISODate UTC del intento de envio"
    },
    detalle_error: {
      bsonType: "string",
      description: "Motivo del fallo cuando estado_envio es 'fallido' (H-11: un fallo de SMTP no debe tumbar el proceso). Opcional: no aplica a un envio exitoso"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

db.notificaciones.insertMany([
  { _id: "NOT-001", alerta_id: "ALR-001", canal: "correo", estado_envio: "enviado", fecha: new Date("2026-09-24T22:47:30-06:00"), esquema_version: 1 }
]);

// ------------------------------------------------------------
// 11. INDICADORES_DIARIOS
// Proposito: rollup materializado que abre el panel del supervisor (UC-06)
// sin recalcular nada dentro de la peticion. _id = fecha (decision de
// contrato: el scaffold de Practica 1 dejaba que Mongo generara un
// ObjectId, lo cual violaba el contrato de identificadores). Se conserva
// tambien el campo fecha (mismo valor que _id) porque el panel consulta
// por ese nombre de campo explicitamente.
// por_severidad solo tiene MEDIA/ALTA/CRITICA (no BAJA): igual que en
// alertas, no existe severidad BAJA porque esos casos nunca llegan a ser
// alerta (UC-02). por_estado si cubre los 5 estados de la maquina de
// alertas.estado, aunque el ejemplo de Practica 1 solo mostraba 3 -- se
// completa aqui para que el rollup sea consistente con el contrato real.
// ------------------------------------------------------------
recrearColeccion("indicadores_diarios", {
  bsonType: "object",
  title: "indicadores_diarios",
  required: ["_id", "fecha", "total_alertas", "por_severidad", "por_estado", "esquema_version"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
      description: "Fecha del rollup, texto YYYY-MM-DD (excepcion al contrato de fechas ISODate)"
    },
    fecha: {
      bsonType: "string",
      pattern: "^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
      description: "Igual a _id; se mantiene como campo explicito para las consultas del panel"
    },
    total_alertas: {
      bsonType: "int",
      minimum: 0,
      description: "Cantidad total de alertas creadas ese dia"
    },
    por_severidad: {
      bsonType: "object",
      required: ["MEDIA", "ALTA", "CRITICA"],
      additionalProperties: false,
      properties: {
        MEDIA: { bsonType: "int", minimum: 0 },
        ALTA: { bsonType: "int", minimum: 0 },
        CRITICA: { bsonType: "int", minimum: 0 }
      },
      description: "Conteo por severidad (sin BAJA: ver nota arriba)"
    },
    por_estado: {
      bsonType: "object",
      required: ["nueva", "en_revision", "confirmada", "descartada", "escalada"],
      additionalProperties: false,
      properties: {
        nueva: { bsonType: "int", minimum: 0 },
        en_revision: { bsonType: "int", minimum: 0 },
        confirmada: { bsonType: "int", minimum: 0 },
        descartada: { bsonType: "int", minimum: 0 },
        escalada: { bsonType: "int", minimum: 0 }
      },
      description: "Conteo por cada estado de la maquina de alertas.estado"
    },
    calculado_en: {
      bsonType: "date",
      description: "ISODate UTC del ultimo calculo del rollup (H-20: 'muestra la marca de tiempo del ultimo calculo'; nunca se recalcula al vuelo en la peticion). Opcional: no esta en CONTRATO-IDS.md, la escribe el pipeline $merge de H-21 cuando corra"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

// Rollup del 2026-09-24 calculado a mano a partir de ALR-001 (la unica
// alerta de esta semilla): 1 alerta critica, confirmada.
db.indicadores_diarios.insertMany([
  {
    _id: "2026-09-24",
    fecha: "2026-09-24",
    total_alertas: 1,
    por_severidad: { MEDIA: 0, ALTA: 0, CRITICA: 1 },
    por_estado: { nueva: 0, en_revision: 0, confirmada: 1, descartada: 0, escalada: 0 },
    esquema_version: 1
  }
]);

// ------------------------------------------------------------
// 12. POLITICA_DETECCION (coleccion de OPERACION, no es una de las 11 de
// dominio -- ver CLAUDE.md: "13 = 11 dominio + 2 operacion"). H-07 la creo
// en scripts/semilla/03-catalogos.js sin validador; se agrega aqui a
// pedido de H-07, leyendo la forma real que ya tiene en base de datos
// (`db.politica_deteccion.findOne()`) para no adivinarla. No se usa
// recrearColeccion(): esta coleccion NUNCA se dropea porque no es
// responsabilidad de este archivo reponer su documento (POL-001, cortes
// de severidad y umbral de creacion de alerta). asegurarValidador() crea
// la coleccion si no existe (ambiente nuevo, 01 corrido antes que 03) o le
// aplica collMod si ya existe (ambiente con el catalogo real de H-07 ya
// cargado), sin tocar los documentos en ningun caso.
// ------------------------------------------------------------
asegurarValidador("politica_deteccion", {
  bsonType: "object",
  title: "politica_deteccion",
  required: ["_id", "descripcion", "vigente", "version", "umbral_alerta", "puntaje_maximo", "cortes_severidad", "notificacion", "esquema_version", "vigente_desde"],
  additionalProperties: false,
  properties: {
    _id: {
      bsonType: "string",
      pattern: "^POL-[0-9]{3}$",
      description: "Identificador legible, ej. POL-001"
    },
    descripcion: {
      bsonType: "string",
      minLength: 1,
      description: "Que cubre esta politica, en lenguaje de negocio"
    },
    vigente: {
      bsonType: "bool",
      description: "Si esta es la politica activa hoy; unica con vigente:true (indice en 02-indices.js)"
    },
    version: {
      bsonType: "int",
      minimum: 1,
      description: "Version de la politica (distinta de esquema_version, que es la del documento)"
    },
    umbral_alerta: {
      bsonType: "int",
      minimum: 0,
      description: "Puntaje minimo para crear una alerta (UC-02: bajo esto, no se crea alerta)"
    },
    puntaje_maximo: {
      bsonType: "int",
      minimum: 1,
      description: "Tope al que se recorta alertas.puntaje (mismo valor que el maximum de esa propiedad)"
    },
    cortes_severidad: {
      bsonType: "array",
      minItems: 1,
      description: "Tramos de puntaje por severidad (MEDIA 40-54, ALTA 55-69, CRITICA 70-100 en la politica vigente)",
      items: {
        bsonType: "object",
        required: ["severidad", "puntaje_minimo", "puntaje_maximo"],
        additionalProperties: false,
        properties: {
          severidad: {
            enum: ["MEDIA", "ALTA", "CRITICA"],
            description: "Mismo enum que alertas.severidad; no existe BAJA (ver esa coleccion)"
          },
          puntaje_minimo: { bsonType: "int", minimum: 0 },
          puntaje_maximo: { bsonType: "int", minimum: 0 }
        }
      }
    },
    notificacion: {
      bsonType: "object",
      required: ["severidad_minima", "ventana_agrupacion_minutos"],
      additionalProperties: false,
      description: "Regla de agrupacion del correo de riesgo critico (UC-08)",
      properties: {
        severidad_minima: {
          enum: ["MEDIA", "ALTA", "CRITICA"],
          description: "Desde que severidad se notifica"
        },
        ventana_agrupacion_minutos: {
          bsonType: "int",
          minimum: 0,
          description: "Ventana para agrupar alertas en un solo correo (UC-08: no inundar)"
        }
      }
    },
    vigente_desde: {
      bsonType: "date",
      description: "ISODate UTC desde cuando rige esta version de la politica"
    },
    esquema_version: {
      bsonType: "int",
      minimum: 1,
      description: "Version del esquema del documento"
    }
  }
});

// Sin insertMany a proposito: ver nota arriba, el documento (POL-001) lo
// puebla H-07 en scripts/semilla/03-catalogos.js.

// ------------------------------------------------------------
// Verificacion general
// ------------------------------------------------------------
print("--------------------------------------------------");
print("clientes: " + db.clientes.countDocuments({}));
print("cuentas: " + db.cuentas.countDocuments({}));
print("perfiles_comportamiento: " + db.perfiles_comportamiento.countDocuments({}));
print("reglas_deteccion: " + db.reglas_deteccion.countDocuments({}) + " (esperado 0 aqui; catalogo real lo puebla H-07 en 03-catalogos.js)");
print("listas_riesgo: " + db.listas_riesgo.countDocuments({}));
print("transacciones: " + db.transacciones.countDocuments({}));
print("agentes: " + db.agentes.countDocuments({}));
print("alertas: " + db.alertas.countDocuments({}));
print("casos: " + db.casos.countDocuments({}));
print("notificaciones: " + db.notificaciones.countDocuments({}));
print("indicadores_diarios: " + db.indicadores_diarios.countDocuments({}));
print("politica_deteccion: " + db.politica_deteccion.countDocuments({}) + " (coleccion de operacion; el documento POL-001 lo puebla H-07 en 03-catalogos.js)");
print("==================================================");
print(" 01-colecciones.js terminado");
print("==================================================");
