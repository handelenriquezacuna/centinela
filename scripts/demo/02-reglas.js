// ============================================================
// Centinela - demo mongosh, paso 2: las reglas con una victima
// ============================================================
//
// Una sola victima: CTA-001 (Maria Rodriguez, 8712-4455, cliente CLI-001).
// Cada bloque inserta una transferencia con fecha de HOY a una hora elegida
// para que una regla (o una combinacion) se vea, y despues consulta la
// alerta que genero D1.
//
// Perfil de CLI-001 (perfiles_comportamiento): promedio 25.000 CRC,
// maximo 100.000 CRC, horario habitual 07:00-19:00 (hora de Costa Rica).
//
// Reglas del catalogo (reglas_deteccion): R-01 monto (8 veces el promedio),
// R-03 destino nuevo (0 transferencias previas en 90 dias), R-05 fuera de
// horario (holgura 120 minutos), R-07 lista de riesgo, R-09 rafaga (3
// transferencias del mismo origen en 30 minutos y 300.000 CRC acumulados).
// Umbral de alerta 35: una transaccion que suma menos de 35 no crea alerta
// y no deja rastro de las reglas que disparo (D1 solo persiste alertas).
//
// Las ventanas miran hacia atras desde la fecha de la transferencia, no
// desde el reloj. Por eso las horas se eligen como fechas de hoy.
//
// Uso (correr 99 antes de cada corrida nueva; ver docs/10-demo-mongosh.md):
//   mongosh "$URI_DEMO" --file scripts/demo/02-reglas.js

load("scripts/demo/00-ayudas.js");

const ORIGEN = "CTA-001";
const SINPE_ORIGEN = "8712-4455";
const CASOS_INSERTADOS = [];

// Consulta la alerta de una transaccion y la imprime con sus reglas.
// Si no hay alerta, lo dice: D1 no guarda las reglas de una transaccion
// que no llega al umbral.
function mirar(id) {
  const alerta = db.alertas.findOne(
    { transaccion_id: id },
    { _id: 1, puntaje: 1, severidad: 1, estado: 1, reglas_disparadas: 1 }
  );
  if (alerta === null) {
    print("  " + id + ": sin alerta (puntaje bajo el umbral 35 o D1 todavia no proceso)");
    return;
  }
  print("  " + alerta._id + " (" + id + ") | puntaje " + alerta.puntaje +
    " | severidad " + alerta.severidad + " | estado " + alerta.estado);
  alerta.reglas_disparadas.forEach(function (regla) {
    print("    " + regla.codigo + " peso " + regla.peso + ": " + regla.explicacion);
  });
}

print("== Perfil de la victima (CLI-001) ==");
printjson(db.perfiles_comportamiento.findOne(
  { _id: "CLI-001" },
  { _id: 1, promedio_crc: 1, maximo_crc: 1, horario_habitual: 1, destinos_frecuentes: 1 }
));

// ------------------------------------------------------------
// 2.1 R-01 monto + R-03 destino nuevo, a las 11:30 (dentro del horario).
// 280.000 es 11,2 veces el promedio; 2233-4455 nunca lo ha usado CTA-001.
// ------------------------------------------------------------
print("");
print("== 2.1 monto 280.000 a destino nuevo 2233-4455, 11:30 ==");
const t21 = transferencia({
  origen: ORIGEN, sinpe_origen: SINPE_ORIGEN, destino: "2233-4455",
  monto: 280000, fecha: hoyCR(11, 30)
});
printjson(t21);
db.transacciones.insertOne(t21);
CASOS_INSERTADOS.push(t21._id);
sleep(2000);
mirar(t21._id);

// ------------------------------------------------------------
// 2.2 Transferencia normal (contraste): 25.000 a un destino ya usado,
// en horario. Ninguna regla dispara.
// ------------------------------------------------------------
print("");
print("== 2.2 contraste normal: 25.000 a 2233-4455, 11:45 ==");
const t22 = transferencia({
  origen: ORIGEN, sinpe_origen: SINPE_ORIGEN, destino: "2233-4455",
  monto: 25000, fecha: hoyCR(11, 45)
});
printjson(t22);
db.transacciones.insertOne(t22);
CASOS_INSERTADOS.push(t22._id);
sleep(2000);
mirar(t22._id);

// ------------------------------------------------------------
// 2.3 R-07 destino en lista de riesgo: 6033-9001 (RSK-001), 13:30.
// Ya tiene historial de CTA-001 (TXN-001 de setiembre), asi que R-03 no
// dispara; solo R-07 aporta 40 y crea alerta MEDIA.
// ------------------------------------------------------------
print("");
print("== 2.3 destino en lista de riesgo 6033-9001, 13:30 ==");
const t23 = transferencia({
  origen: ORIGEN, sinpe_origen: SINPE_ORIGEN, destino: "6033-9001",
  monto: 50000, fecha: hoyCR(13, 30)
});
printjson(t23);
db.transacciones.insertOne(t23);
CASOS_INSERTADOS.push(t23._id);
sleep(2000);
mirar(t23._id);

// ------------------------------------------------------------
// 2.4 y 2.5 R-05 fuera de horario sin nada mas: 21:30 y 21:40. 110.000 cada
// una. R-05 aporta 15, por debajo del umbral: no hay alerta. Son las dos
// primeras de la rafaga de 2.6.
// ------------------------------------------------------------
print("");
print("== 2.4 rafaga, primera: 110.000 a 2233-4455, 21:30 (fuera de horario) ==");
const t24 = transferencia({
  origen: ORIGEN, sinpe_origen: SINPE_ORIGEN, destino: "2233-4455",
  monto: 110000, fecha: hoyCR(21, 30)
});
printjson(t24);
db.transacciones.insertOne(t24);
CASOS_INSERTADOS.push(t24._id);
sleep(2000);
mirar(t24._id);

print("");
print("== 2.5 rafaga, segunda: 110.000 a 2233-4455, 21:40 ==");
const t25 = transferencia({
  origen: ORIGEN, sinpe_origen: SINPE_ORIGEN, destino: "2233-4455",
  monto: 110000, fecha: hoyCR(21, 40)
});
printjson(t25);
db.transacciones.insertOne(t25);
CASOS_INSERTADOS.push(t25._id);
sleep(2000);
mirar(t25._id);

// ------------------------------------------------------------
// 2.6 R-09 velocidad + R-05 fuera de horario, a las 21:50. Tercera del
// mismo origen en 30 minutos: 3 transferencias y 330.000 acumulados (sobre
// el piso de 300.000). R-09 aporta 30, R-05 aporta 15: 45, MEDIA.
// ------------------------------------------------------------
print("");
print("== 2.6 rafaga, tercera: 110.000 a 2233-4455, 21:50 ==");
const t26 = transferencia({
  origen: ORIGEN, sinpe_origen: SINPE_ORIGEN, destino: "2233-4455",
  monto: 110000, fecha: hoyCR(21, 50)
});
printjson(t26);
db.transacciones.insertOne(t26);
CASOS_INSERTADOS.push(t26._id);
sleep(2000);
mirar(t26._id);

// ------------------------------------------------------------
// 2.7 R-03 destino nuevo + R-05 fuera de horario, a las 23:10. 7001-2020
// nunca se ha usado: R-03 aporta 25, R-05 aporta 15: 40, MEDIA.
// ------------------------------------------------------------
print("");
print("== 2.7 destino nuevo 7001-2020 fuera de horario, 23:10 ==");
const t27 = transferencia({
  origen: ORIGEN, sinpe_origen: SINPE_ORIGEN, destino: "7001-2020",
  monto: 25000, fecha: hoyCR(23, 10)
});
printjson(t27);
db.transacciones.insertOne(t27);
CASOS_INSERTADOS.push(t27._id);
sleep(2000);
mirar(t27._id);

// ------------------------------------------------------------
// Resumen: ids insertados por este script. 99-limpiar.js borra estas
// transacciones por fecha de hoy y sus alertas.
// ------------------------------------------------------------
print("");
print("== Resumen: transacciones insertadas por 02 ==");
print(CASOS_INSERTADOS.join(" "));
print("alertas creadas para estas transacciones: " +
  db.alertas.countDocuments({ transaccion_id: { $in: CASOS_INSERTADOS } }));
