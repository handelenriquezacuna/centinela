// ============================================================
// H-07 · Catalogo de reglas de deteccion y politica de severidad
//
// Las reglas viven en la base, no en el codigo: cambiar un umbral, un peso o
// apagar una regla NO requiere desplegar (regla de oro de docs/04-arquitectura.md
// y criterio de H-07).
//
// Este script llena las dos colecciones de configuracion del motor:
//
//   reglas_deteccion    6 reglas con codigo, descripcion, tipo, umbral, peso, estado
//                       (activa) y version. La coleccion, su validador y sus indices
//                       son de H-01 (01-colecciones.js / 02-indices.js), que la
//                       deja vacia a proposito; los documentos son de H-07.
//   politica_deteccion  1 documento con los cortes de severidad y el umbral a
//                       partir del cual se crea alerta. Estos numeros son
//                       decisiones de negocio, y por eso viven aqui y no en
//                       config/centinela.yml (hay una prueba que lo verifica:
//                       pruebas/contrato/prueba_configuracion.py). El validador de
//                       esta coleccion tambien es de H-01, leido de la forma real
//                       del documento que escribe este script.
//
// Como se corre (igual en macOS, Linux y Windows; scripts/ esta montado como
// /scripts dentro del contenedor). Va DESPUES de 01-colecciones.js y 02-indices.js:
//
//   docker compose -f infra/docker-compose.yml exec mongo1 \
//     mongosh --port 27018 --quiet --file /scripts/semilla/03-catalogos.js
//
// Idempotente: reemplaza por _id, asi que correrlo dos veces deja lo mismo. No borra
// colecciones ni bases: solo escribe estos 7 documentos. Ojo con una cosa: devuelve el
// catalogo a su linea base, de modo que si un supervisor ajusto un umbral en caliente
// (UC-07), volver a correr esto lo revierte.
//
// Contratos que respeta (CLAUDE.md):
//   - base de datos `antifraude`
//   - `_id` cadenas legibles (REG-001..REG-006), nunca ObjectId
//   - `esquema_version` entero en todo documento
//   - fechas ISODate en UTC
//   - dinero: hoy ningun umbral guarda colones (son multiplos, cantidades, minutos y
//     proporciones); el dia que uno los guarde va como NumberLong("750000") y jamas
//     como 750000 pelado, que mongosh guardaria perdiendo el contrato de 64 bits
//   - snake_case en espanol en los nombres de campo
// ============================================================

// La base es `antifraude`. El nombre se puede sobreescribir para correr el catalogo
// contra una base de pruebas, igual que CENTINELA_MONGO__BASE en el YAML:
//   mongosh ... --eval 'BASE_CENTINELA = "antifraude_pruebas"' --file /scripts/semilla/03-catalogos.js
const NOMBRE_BASE = typeof BASE_CENTINELA === "string" ? BASE_CENTINELA : "antifraude";
const antifraude = db.getSiblingDB(NOMBRE_BASE);

const ESQUEMA_VERSION = 1;

// La politica entra en vigencia antes del caso documentado (24 de setiembre de 2026,
// 22:47 hora de Costa Rica): una alerta solo puede evaluarse con la politica que
// estaba vigente cuando ocurrio la transaccion.
const VIGENTE_DESDE = ISODate("2026-09-01T00:00:00Z");

// Campos que el validador de `reglas_deteccion` acepta (additionalProperties: false).
// Si una regla trae un campo que no esta aqui, este script lo dice con nombre y apellido
// antes de escribir, en vez de dejar que Mongo responda "Document failed validation".
//
// `version` no es opcional: el validador de H-01 lo pide en `required`, y con razon. La
// alerta copia la version con la que se evaluo para poder explicarse dentro de un ano
// aunque la regla ya haya cambiado (H-16, UC-07); una regla sin version degrada ese
// requisito en silencio. Toda regla la lleva desde que nace.
//
// `parametros` es opcional y solo aparece en las reglas que tienen una perilla secundaria
// ademas del `umbral`: una ventana de minutos, una ventana de dias, una cantidad de
// saltos. Existe para que H-08 las lea de la base en vez de codificarlas en Python, que
// es el punto entero de esta historia. Donde no hay nada que decir, el campo no va.
const CAMPOS_VALIDADOR = [
  "_id", "codigo", "descripcion", "tipo", "umbral", "parametros", "peso", "activa",
  "version", "esquema_version"
];

// ------------------------------------------------------------
// 1. REGLAS DE DETECCION
//
// Los cinco codigos cortos que el proyecto ya usa son R-01, R-03, R-05, R-07 y R-09
// (docs/02-casos-de-uso.md, tabla del UC-02). La sexta sigue la numeracion impar: R-11.
//
//   codigo       codigo corto tal como aparece en los casos de uso
//   descripcion  en una linea, es lo que el portal le muestra al agente
//   tipo         familia de la regla; el evaluador de H-08 despacha por este campo
//   umbral       EL numero que el supervisor ajusta. Su unidad depende del tipo: un
//                multiplo, una cantidad, minutos o una proporcion. Si algun dia un
//                umbral guarda dinero, va como NumberLong("750000") y no como 750000
//   parametros   perillas secundarias, solo en las reglas que las necesitan
//   peso         aporte al puntaje cuando la regla dispara
//   activa       estado de la regla. false = apagada, el evaluador la ignora
//   version      version de la regla; la alerta guarda con cual se evaluo (H-16)
//
// Las tres primeras son las ya documentadas en scripts/practica1/CONTRATO-IDS.md y en
// el UC-02: se conservan codigo, descripcion y peso al pie de la letra, porque el caso
// de TXN-001 depende de que sumen exactamente 75.
// ------------------------------------------------------------
const reglas = [
  {
    _id: "REG-001",
    codigo: "R-01",
    descripcion: "Monto sobre 10x el promedio del cliente",
    tipo: "monto",
    // Veces el `promedio_crc` del perfil del cliente. TXN-001 va 16,6x: dispara.
    // Subirlo a 20 hace que TXN-001 deje de disparar esta regla, y eso es H-07 entera:
    // el comportamiento del motor cambio sin tocar una linea de codigo.
    umbral: 10,
    peso: 35,
    activa: true,
    version: 1,
    esquema_version: ESQUEMA_VERSION
  },
  {
    _id: "REG-002",
    codigo: "R-03",
    descripcion: "Destino que el cliente nunca ha usado",
    tipo: "destino",
    // Maximo de transferencias previas al mismo destino para que siga contando como
    // nuevo. Subirlo a 1 hace que un destino usado una sola vez ya no cuente como nuevo.
    umbral: 0,
    // Cuanto historial se mira para decidir si el destino es nuevo: los 90 dias del
    // perfil de UC-02. Sin este parametro, H-08 tendria que codificar el 90 en Python.
    parametros: { dias_historial: 90 },
    peso: 25,
    activa: true,
    version: 1,
    esquema_version: ESQUEMA_VERSION
  },
  {
    _id: "REG-003",
    codigo: "R-05",
    descripcion: "Transaccion fuera del horario habitual",
    tipo: "horario",
    // Holgura en minutos fuera de la ventana habitual del cliente. La ventana NO es un
    // numero global: sale de `perfiles_comportamiento.horario_habitual` (07:00-19:00
    // para CLI-001) y esta en hora local de Costa Rica, mientras la transaccion esta en
    // UTC. Convertir es responsabilidad del evaluador: 22:47 en San Jose es 04:47 UTC
    // del dia siguiente. Con 0 cualquier minuto fuera de la ventana dispara; subirla a
    // 60 le perdona una hora de desfase y baja el ruido que UC-06 le achaca a R-05.
    umbral: 0,
    peso: 15,
    activa: true,
    version: 1,
    esquema_version: ESQUEMA_VERSION
  },
  {
    _id: "REG-004",
    codigo: "R-07",
    descripcion: "Destino en lista de riesgo vigente al momento de la transaccion",
    tipo: "lista_riesgo",
    // Coincidencias minimas en `listas_riesgo` (con `vigente: true`) para disparar. Hoy
    // una cuenta tiene a lo sumo una entrada, asi que 1 significa "esta en la lista".
    umbral: 1,
    // Peso mas alto del catalogo, como pide UC-09: una cuenta ya senalada como mula es
    // la senal mas fuerte que hay, y sola alcanza para abrir alerta (40 >= umbral_alerta).
    peso: 40,
    activa: true,
    version: 1,
    esquema_version: ESQUEMA_VERSION
  },
  {
    _id: "REG-005",
    codigo: "R-09",
    descripcion: "Velocidad anomala: rafaga de transferencias del mismo origen",
    tipo: "velocidad",
    // El patron de fragmentacion de UC-04: varias transferencias seguidas, cada una
    // chica, para que ninguna levante sospecha sola. Umbral = cantidad minima de
    // transferencias del mismo origen dentro de la ventana.
    umbral: 3,
    // La ventana en la que se cuentan esas transferencias, y un piso de dinero para que
    // tres transferencias de mil colones no levanten una alerta. El monto va con
    // NumberLong("300000"): es dinero, entero de 64 bits, y la forma numerica pelada
    // (300000) mongosh la avisa como perdida de precision.
    parametros: { ventana_minutos: 30, monto_acumulado_crc: NumberLong("300000") },
    peso: 30,
    activa: true,
    version: 1,
    esquema_version: ESQUEMA_VERSION
  },
  {
    _id: "REG-006",
    codigo: "R-11",
    descripcion: "El destino reenvia casi todo lo recibido en pocos minutos",
    tipo: "red_mulas",
    // La cadena de mulas de UC-04: 6033-9001 recibe 750.000 y reenvia 740.000 (98,7 %)
    // cuatro minutos despues. Umbral = proporcion minima reenviada.
    umbral: 0.8,
    // En cuanto tiempo tiene que ocurrir ese reenvio y cuantos saltos se piden para
    // llamarlo cadena. Los tres saltos de UC-04 caen dentro: +4, +9 y +11 minutos.
    parametros: { ventana_minutos: 15, saltos_minimos: 2 },
    peso: 20,
    // Nace apagada, y es a proposito: el reenvio ocurre DESPUES de la transaccion que se
    // esta evaluando, asi que en linea (D1) esta regla todavia no tiene con que disparar.
    // Sirve para el recorrido de la red de mulas (H-23) y para la medicion retrospectiva
    // (H-10). Encenderla es un solo updateOne, sin desplegar nada: es la demostracion
    // del criterio "se pueden activar y desactivar" de H-07.
    activa: false,
    version: 1,
    esquema_version: ESQUEMA_VERSION
  }
];

// ------------------------------------------------------------
// 2. POLITICA DE DETECCION
//
// Un solo documento vigente. Aca estan los dos numeros que deciden que le llega al
// agente: a partir de cuanto se crea una alerta, y con que puntaje cada alerta es
// MEDIA, ALTA o CRITICA.
//
// Por que en Mongo y no en el YAML: son decisiones del negocio que cambian durante un
// turno. Un supervisor que ve 66 % de falsos positivos (UC-06) sube `umbral_alerta` y el
// motor lo toma sin reiniciar. Si esto viviera en el YAML, cambiarlo seria un despliegue.
//
// Las tres severidades van en MAYUSCULA porque asi las guarda `alertas.severidad` y asi
// las cuenta `indicadores_diarios.por_severidad` (app/modelos/estados.py). No hay BAJA:
// si el puntaje no llega al umbral no se crea alerta (UC-02), asi que una alerta de
// severidad baja no puede existir.
// ------------------------------------------------------------
const politica = {
  _id: "POL-001",
  descripcion: "Cortes de severidad y umbral de creacion de alerta",
  vigente: true,
  version: 1,
  // Minimo para que una transaccion se convierta en alerta. Por debajo de esto la
  // transaccion no se pierde: queda en `transacciones` para la busqueda forense (UC-05).
  // Con estos pesos, ninguna regla sola llega a 40 salvo R-07 (lista de riesgo): hace
  // falta que dos senales coincidan, que es la defensa mas barata contra falsos positivos.
  umbral_alerta: 40,
  // El puntaje se topa aqui. No es capricho: `alertas.puntaje` esta validado hasta 100 y
  // los pesos se pasan de ahi. Los dos numeros, para que nadie los confunda: las 6 reglas
  // del catalogo suman 165, y las 5 que nacen activas suman 145 (R-11 nace apagada).
  // El evaluador (H-08) calcula min(suma_de_pesos, puntaje_maximo).
  puntaje_maximo: 100,
  // Tramos contiguos que cubren de `umbral_alerta` hasta `puntaje_maximo`, sin huecos ni
  // traslapes. Limites inclusivos en los dos extremos.
  //
  // CRITICA arranca en 70 y no en 75 a proposito: el caso documentado (R-01 + R-03 + R-05
  // = 75) tiene que caer COMODO dentro de CRITICA, no justo en el borde. Asi el dia que
  // el supervisor le baje el peso a R-05 (el ejemplo de UC-07), el caso sigue siendo
  // critico y la demo no se cae por cinco puntos.
  cortes_severidad: [
    { severidad: "MEDIA", puntaje_minimo: 40, puntaje_maximo: 54 },
    { severidad: "ALTA", puntaje_minimo: 55, puntaje_maximo: 69 },
    { severidad: "CRITICA", puntaje_minimo: 70, puntaje_maximo: 100 }
  ],
  // A partir de que severidad se despierta a la guardia por correo (D2, UC-08) y en que
  // ventana se agrupan las alertas para no inundar la bandeja. Tambien son numeros de
  // negocio, asi que tampoco pueden vivir en el YAML.
  notificacion: { severidad_minima: "CRITICA", ventana_agrupacion_minutos: 5 },
  esquema_version: ESQUEMA_VERSION,
  vigente_desde: VIGENTE_DESDE
};

// ------------------------------------------------------------
// 3. COMPROBACIONES ANTES DE ESCRIBIR
// ------------------------------------------------------------
function exigir(condicion, mensaje) {
  if (!condicion) {
    throw new Error("H-07: " + mensaje);
  }
}

reglas.forEach(function (regla) {
  Object.keys(regla).forEach(function (campo) {
    exigir(CAMPOS_VALIDADOR.indexOf(campo) !== -1,
      regla._id + " trae el campo `" + campo + "`, que el validador de reglas_deteccion " +
      "no acepta (additionalProperties: false). El validador es de H-01: hay que " +
      "coordinar con el, no cambiarlo desde este script");
  });
});

// ------------------------------------------------------------
// 4. ESCRITURA
// ------------------------------------------------------------
const escrituraReglas = antifraude.reglas_deteccion.bulkWrite(
  reglas.map(function (regla) {
    return { replaceOne: { filter: { _id: regla._id }, replacement: regla, upsert: true } };
  }),
  { ordered: true }
);

const escrituraPolitica = antifraude.politica_deteccion.replaceOne(
  { _id: politica._id },
  politica,
  { upsert: true }
);

// ------------------------------------------------------------
// 5. COMPROBACIONES SOBRE LO QUE QUEDO GUARDADO
//
// El script falla con error si el catalogo que acabo de escribir no sostiene el caso
// documentado. Es la unica forma de que "75 es CRITICA" no se rompa en silencio el dia
// que alguien mueva un peso o un corte.
// ------------------------------------------------------------

// La severidad segun la politica recien guardada. La implementacion de verdad es la de
// H-08 en Python; esta copia de tres lineas existe solo para comprobar los datos aqui.
function severidadDe(puntaje, politicaVigente) {
  const tramo = politicaVigente.cortes_severidad.find(function (corte) {
    return puntaje >= corte.puntaje_minimo && puntaje <= corte.puntaje_maximo;
  });
  return tramo ? tramo.severidad : null;
}

const politicaGuardada = antifraude.politica_deteccion.findOne({ vigente: true });
const reglasGuardadas = antifraude.reglas_deteccion.find({}).sort({ codigo: 1 }).toArray();

exigir(politicaGuardada !== null, "no quedo ninguna politica vigente");
exigir(antifraude.politica_deteccion.countDocuments({ vigente: true }) === 1,
  "tiene que haber exactamente una politica vigente");
exigir(reglasGuardadas.length >= 6, "H-07 pide al menos 6 reglas y quedaron " + reglasGuardadas.length);
exigir(new Set(reglasGuardadas.map(function (r) { return r.codigo; })).size === reglasGuardadas.length,
  "hay codigos de regla repetidos");
reglasGuardadas.forEach(function (regla) {
  exigir(/^R-\d{2}$/.test(regla.codigo), "codigo mal formado: " + regla._id);
  exigir(typeof regla.descripcion === "string" && regla.descripcion.length > 0,
    regla._id + " sin descripcion");
  exigir(typeof regla.tipo === "string" && regla.tipo.length > 0, regla._id + " sin tipo");
  exigir(typeof regla.umbral === "number", regla._id + " sin umbral");
  exigir(Number.isInteger(regla.peso) && regla.peso >= 0 && regla.peso <= 100,
    regla._id + " con peso fuera de 0..100");
  exigir(typeof regla.activa === "boolean", regla._id + " sin estado activa/inactiva");
  exigir(Number.isInteger(regla.version) && regla.version >= 1,
    regla._id + " sin version; H-16 la necesita dentro de la alerta");
  exigir(regla.esquema_version === ESQUEMA_VERSION, regla._id + " sin esquema_version");
});

// Las tres severidades del enum de app/modelos/estados.py, ni una mas ni una menos.
exigir(
  politicaGuardada.cortes_severidad.map(function (c) { return c.severidad; }).join(",") ===
    "MEDIA,ALTA,CRITICA",
  "los cortes tienen que ser exactamente MEDIA, ALTA y CRITICA, en ese orden y en mayuscula"
);

// Los cortes tienen que ser contiguos, sin huecos ni traslapes, y empezar justo en el
// umbral de alerta: un puntaje que crea alerta pero no cae en ningun tramo seria una
// alerta sin severidad.
exigir(politicaGuardada.cortes_severidad[0].puntaje_minimo === politicaGuardada.umbral_alerta,
  "el primer corte tiene que empezar en umbral_alerta");
politicaGuardada.cortes_severidad.forEach(function (corte, indice) {
  exigir(corte.puntaje_minimo <= corte.puntaje_maximo, "corte invertido: " + corte.severidad);
  if (indice > 0) {
    const anterior = politicaGuardada.cortes_severidad[indice - 1];
    exigir(corte.puntaje_minimo === anterior.puntaje_maximo + 1,
      "hueco o traslape entre " + anterior.severidad + " y " + corte.severidad);
  }
});
exigir(
  politicaGuardada.cortes_severidad[politicaGuardada.cortes_severidad.length - 1].puntaje_maximo
    === politicaGuardada.puntaje_maximo,
  "el ultimo corte tiene que llegar hasta puntaje_maximo"
);

// El caso documentado, que es el que se ensena en la demo: TXN-001 dispara R-01 + R-03 +
// R-05, suma 75 y es CRITICA (UC-02 y ALR-001).
//
// Las otras tres reglas no disparan en TXN-001, y cada una por su razon: R-09 porque
// CTA-001 hizo una sola transferencia (no hay rafaga), R-11 porque esta apagada, y R-07
// porque las cuentas de la cadena de UC-04 se reportan a las 23:10, DESPUES de la
// transaccion de las 22:47. Ese "vigente al momento de la transaccion" no es adorno: si
// el evaluador compara contra la lista de hoy en vez de contra la de las 22:47, TXN-001
// da 115 y el caso documentado deja de cuadrar.
const pesoDe = function (codigo) {
  const regla = reglasGuardadas.find(function (r) { return r.codigo === codigo; });
  exigir(regla !== undefined, "falta la regla " + codigo + ", que el UC-02 da por sentada");
  exigir(regla.activa, "la regla " + codigo + " tiene que estar activa para el caso de UC-02");
  return regla.peso;
};
const puntajeCaso = pesoDe("R-01") + pesoDe("R-03") + pesoDe("R-05");
exigir(puntajeCaso === 75, "R-01 + R-03 + R-05 tiene que dar 75 y dio " + puntajeCaso);
exigir(severidadDe(puntajeCaso, politicaGuardada) === "CRITICA",
  "puntaje 75 tiene que caer en CRITICA con estos cortes");
exigir(severidadDe(puntajeCaso, politicaGuardada) === politicaGuardada.notificacion.severidad_minima,
  "el caso de UC-02 tiene que disparar el correo de D2 (UC-08)");

// ------------------------------------------------------------
// 6. RESUMEN EN PANTALLA
// ------------------------------------------------------------
const columna = function (valor, ancho) {
  return String(valor === null || valor === undefined ? "-" : valor).padEnd(ancho);
};

// A mano y no con JSON.stringify: un NumberLong serializado a JSON sale como
// {"high":0,"low":300000}, que no le dice nada a nadie leyendo la salida.
const parametrosLegibles = function (parametros) {
  if (!parametros) { return "-"; }
  return Object.keys(parametros).map(function (clave) {
    return clave + "=" + parametros[clave];
  }).join(" ");
};

print("");
print("H-07 catalogo cargado en la base `" + NOMBRE_BASE + "`");
print("  reglas_deteccion    nuevas " + escrituraReglas.upsertedCount +
  ", devueltas a la linea base " + escrituraReglas.modifiedCount +
  ", sin cambios " + (escrituraReglas.matchedCount - escrituraReglas.modifiedCount) +
  ", total " + antifraude.reglas_deteccion.countDocuments({}));
print("  politica_deteccion  nuevas " + escrituraPolitica.upsertedCount +
  ", devueltas a la linea base " + escrituraPolitica.modifiedCount +
  ", sin cambios " + (escrituraPolitica.matchedCount - escrituraPolitica.modifiedCount) +
  ", total " + antifraude.politica_deteccion.countDocuments({}));
print("");
print("  codigo  tipo          umbral  peso  estado    ver  _id      parametros");
reglasGuardadas.forEach(function (regla) {
  print("  " + columna(regla.codigo, 8) + columna(regla.tipo, 14) + columna(regla.umbral, 8) +
    columna(regla.peso, 6) + columna(regla.activa ? "activa" : "inactiva", 10) +
    columna(regla.version, 5) + columna(regla._id, 9) + parametrosLegibles(regla.parametros));
});
const sumaPesos = function (lista) {
  return lista.reduce(function (total, r) { return total + r.peso; }, 0);
};
print("  pesos: las 6 reglas suman " + sumaPesos(reglasGuardadas) + ", las activas suman " +
  sumaPesos(reglasGuardadas.filter(function (r) { return r.activa; })) +
  ", el puntaje se topa en " + politicaGuardada.puntaje_maximo);
print("");
print("  umbral de alerta    " + politicaGuardada.umbral_alerta +
  " (por debajo no se crea alerta; la transaccion queda para consulta forense)");
politicaGuardada.cortes_severidad.forEach(function (corte) {
  print("  severidad " + columna(corte.severidad, 9) + corte.puntaje_minimo + " a " +
    corte.puntaje_maximo);
});
print("  correo de guardia   desde " + politicaGuardada.notificacion.severidad_minima +
  ", agrupando " + politicaGuardada.notificacion.ventana_agrupacion_minutos + " minutos");
print("");
print("  caso UC-02 / ALR-001: R-01 + R-03 + R-05 = " + puntajeCaso + " -> " +
  severidadDe(puntajeCaso, politicaGuardada) + " (comprobado)");

// Lo que este script NO puede hacer solo, dicho en voz alta en vez de quedar en un
// comentario que nadie lee.
const pendientes = [];
const tieneIndice = function (coleccion, campo) {
  return antifraude.getCollection(coleccion).getIndexes().some(function (indice) {
    return Object.keys(indice.key).indexOf(campo) !== -1;
  });
};
if (!tieneIndice("politica_deteccion", "vigente")) {
  pendientes.push("politica_deteccion sin indice unico parcial sobre `vigente`, que es lo " +
    "que garantiza una sola politica vigente");
}
if (pendientes.length > 0) {
  print("");
  print("  pendiente de coordinar, no lo resuelve este script:");
  pendientes.forEach(function (pendiente) { print("    - " + pendiente); });
}
print("");
