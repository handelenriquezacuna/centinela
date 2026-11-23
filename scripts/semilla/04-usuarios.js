// ============================================================
// H-25 · Usuarios de base de datos y roles con privilegios minimos
//
// Crea (o actualiza, si ya existen) los roles y los usuarios de Centinela sobre la
// base `antifraude`. Idempotente: correrlo de nuevo deja lo mismo y, de paso, rota las
// contrasenas si las variables de entorno cambiaron.
//
// Las contrasenas NO estan aqui: salen de variables de entorno al correr el script
// (las genera `python tareas.py asegurar` en .env, que git ignora).
//
//   MONGO_CLAVE_APP        usuario centinela_app
//   MONGO_CLAVE_ANALISTA   usuario centinela_analista
//   MONGO_CLAVE_ADMIN      usuario centinela_admin
//   MONGO_CLAVE_PRUEBAS    usuario centinela_pruebas (opcional, ver abajo)
//
// Los usuarios viven en `antifraude`: se autentican con authSource=antifraude.
//
// Roles:
//
//   centinela_app (rol propio centinela_app_rol). Lo que usa el proceso: find, insert,
//     update y remove en toda coleccion de antifraude; changeStream (SSE y disparador
//     D1); indexStats, listCollections y listIndexes (red de mulas y diagnostico). Las
//     transacciones y el `$merge` de indicadores no piden mas: son insert/update/find.
//     NO tiene: createCollection, createIndex, dropCollection, collMod, dropDatabase,
//     ni nada sobre usuarios. El esquema lo cambia el admin, no la app.
//   centinela_analista: el rol integrado `read` sobre antifraude. Compass y la defensa.
//   centinela_admin: el rol integrado `dbOwner` de antifraude (mas la temporal, abajo), que es dbAdmin +
//     userAdmin + readWrite. readWrite hace falta porque las semillas (03) insertan y
//     las migraciones actualizan; dbAdmin y userAdmin son los de esquema y usuarios.
//
// Fuera de antifraude, nada: ningun usuario ve otras bases. Dos excepciones deliberadas,
// ambas sobre bases desechables:
//   - centinela_admin tambien es dbOwner de antifraude_validadores_tmp, la base
//     temporal que usa `python tareas.py validadores` (T-24).
//   - centinela_pruebas: dbOwner de BASES_PRUEBAS, las bases que la suite borra y
//     recrea. No toca antifraude. Si falta MONGO_CLAVE_PRUEBAS no se crea. Una prueba de
//     arquitectura exige que toda base `antifraude_pruebas*` que use la suite este en
//     esa lista.
// Esos roles sobre bases ajenas solo se pueden dar con la autenticacion apagada (o con
// un usuario que administre esas bases): en una segunda corrida con auth el script no
// los vuelve a dar, porque ya los tienen.
// ============================================================

const BASE = "antifraude";
const BASE_TEMPORAL_VALIDADORES = "antifraude_validadores_tmp";
const BASES_PRUEBAS = [
  "antifraude_pruebas",
  "antifraude_pruebas_busqueda",
  "antifraude_pruebas_busqueda_volumen",
  "antifraude_pruebas_carga",
  "antifraude_pruebas_d1",
  "antifraude_pruebas_detalle",
  "antifraude_pruebas_e2e",
  "antifraude_pruebas_efectividad",
  "antifraude_pruebas_explain",
  "antifraude_pruebas_h14",
  "antifraude_pruebas_indicadores",
  "antifraude_pruebas_migracion",
  "antifraude_pruebas_migracion_002",
  "antifraude_pruebas_red_mulas",
  "antifraude_pruebas_listas_riesgo",
  "antifraude_pruebas_rezago_volumen",
  "antifraude_pruebas_semilla",
  "antifraude_pruebas_validadores",
  BASE_TEMPORAL_VALIDADORES, // tambien la usan las pruebas de `tareas.py validadores`
];
const destino = db.getSiblingDB(BASE);

function clave(nombre, obligatoria) {
  const valor = process.env[nombre];
  if (!valor) {
    if (obligatoria) throw new Error("falta la variable de entorno " + nombre);
    return null;
  }
  return valor;
}

// El rol se recrea en vez de usar updateRole: la accion que exige updateRole no esta en
// userAdmin, asi que centinela_admin no podria volver a correr este script. dropRole lo
// quita de los usuarios que lo tienen; asegurarUsuario se los vuelve a dar enseguida.
function asegurarRol(nombre, privilegios, roles) {
  const existia = destino.getRole(nombre) !== null;
  if (existia) destino.dropRole(nombre);
  destino.createRole({ role: nombre, privileges: privilegios, roles: roles });
  print("rol " + nombre + ": " + (existia ? "recreado" : "creado"));
}

function asegurarUsuario(base, nombre, pwd, roles) {
  const objetivo = db.getSiblingDB(base);
  const actual = objetivo.getUser(nombre);
  if (actual) {
    // Contrasena y roles por separado: updateUser con `roles` reemplaza la lista y
    // userAdmin no alcanza para eso; grant y revoke si. Solo se da lo que falta: un rol
    // sobre una base ajena que ya se tiene no se vuelve a pedir.
    objetivo.changeUserPassword(nombre, pwd);
    const clave = (r) => r.role + "@" + r.db;
    const deseados = roles.map(clave);
    const tenidos = actual.roles.map(clave);
    const sobrantes = actual.roles.filter((r) => !deseados.includes(clave(r)));
    const faltantes = roles.filter((r) => !tenidos.includes(clave(r)));
    if (sobrantes.length > 0) objetivo.revokeRolesFromUser(nombre, sobrantes);
    if (faltantes.length > 0) objetivo.grantRolesToUser(nombre, faltantes);
    print("usuario " + nombre + ": actualizado");
  } else {
    objetivo.createUser({ user: nombre, pwd: pwd, roles: roles });
    print("usuario " + nombre + ": creado");
  }
}

const claveApp = clave("MONGO_CLAVE_APP", true);
const claveAnalista = clave("MONGO_CLAVE_ANALISTA", true);
const claveAdmin = clave("MONGO_CLAVE_ADMIN", true);
const clavePruebas = clave("MONGO_CLAVE_PRUEBAS", false);

// `collection: ""` = todas las colecciones de la base.
asegurarRol(
  "centinela_app_rol",
  [
    {
      resource: { db: BASE, collection: "" },
      actions: [
        "find", "insert", "update", "remove",
        "changeStream",
        "indexStats",
        "listCollections", "listIndexes",
      ],
    },
  ],
  []
);

asegurarUsuario(BASE, "centinela_app", claveApp, [{ role: "centinela_app_rol", db: BASE }]);
asegurarUsuario(BASE, "centinela_analista", claveAnalista, [{ role: "read", db: BASE }]);
asegurarUsuario(BASE, "centinela_admin", claveAdmin, [
  { role: "dbOwner", db: BASE },
  { role: "dbOwner", db: BASE_TEMPORAL_VALIDADORES },
]);

if (clavePruebas) {
  try {
    // dbOwner no trae `indexStats` (lo usan las pruebas de la red de mulas): rol propio,
    // definido en admin porque abarca varias bases.
    const admin = db.getSiblingDB("admin");
    if (admin.getRole("centinela_pruebas_rol")) admin.dropRole("centinela_pruebas_rol");
    admin.createRole({
      role: "centinela_pruebas_rol",
      privileges: BASES_PRUEBAS.map((base) => ({
        resource: { db: base, collection: "" },
        actions: ["indexStats"],
      })),
      roles: [],
    });
    asegurarUsuario(
      BASE,
      "centinela_pruebas",
      clavePruebas,
      BASES_PRUEBAS.map((base) => ({ role: "dbOwner", db: base })).concat([
        { role: "centinela_pruebas_rol", db: "admin" },
      ])
    );
  } catch (e) {
    if (e.codeName !== "Unauthorized") throw e;
    print("usuario centinela_pruebas: omitido (hace falta la auth apagada para tocarlo)");
  }
}

print("usuarios listos");
