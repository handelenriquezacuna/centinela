// ============================================================
// Centinela - demo mongosh: ayudas
// ============================================================
//
// Solo ahorran teclear. No esconden el insertOne: 01 y 02 lo muestran
// completo y lo imprimen antes de escribir.
//
// Se carga desde los scripts de la demo con:
//   load("scripts/demo/00-ayudas.js")
//
// Las fechas son ISODate en UTC. Costa Rica es UTC-06:00 todo el ano
// (sin horario de verano), asi que la hora local se pasa a UTC restando
// 6 horas, y la zona no depende del nodo donde corre mongosh.

// Desfase fijo de Costa Rica respecto de UTC, en horas.
const DESFASE_CR_HORAS = 6;

// Fecha de hoy en Costa Rica, a una hora local dada, como ISODate UTC.
// Ejemplo: hoyCR(22, 47) -> ISODate del dia de hoy a las 22:47 de CR.
function hoyCR(hora, minuto) {
  const ahoraCR = new Date(Date.now() - DESFASE_CR_HORAS * 3600 * 1000);
  return new Date(Date.UTC(
    ahoraCR.getUTCFullYear(),
    ahoraCR.getUTCMonth(),
    ahoraCR.getUTCDate(),
    hora + DESFASE_CR_HORAS,
    minuto,
    0, 0
  ));
}

// Siguiente id TXN-NNNNNN (6 digitos). Toma el mayor id de 6 digitos ya
// existente y le suma 1. Si no hay ninguno, devuelve TXN-000001.
// Los ids de 3 digitos de la semilla (TXN-001..TXN-007) no cuentan.
function siguienteTxn() {
  const mayor = db.transacciones
    .find({ _id: { $regex: /^TXN-\d{6}$/ } }, { _id: 1 })
    .sort({ _id: -1 })
    .limit(1)
    .toArray();
  if (mayor.length === 0) {
    return "TXN-000001";
  }
  const numero = parseInt(mayor[0]._id.slice(4), 10);
  return "TXN-" + String(numero + 1).padStart(6, "0");
}

// Arma el documento de una transferencia que cumple el validador de
// `transacciones`:
//   monto_crc  NumberLong (entero de 64 bits, nunca double)
//   moneda     "CRC"
//   canal      "sinpe_movil"
//   fecha      ISODate UTC
//   esquema_version 1
// No inserta nada. Hay que llamar insertOne con el resultado.
// Ojo: el id sale de siguienteTxn(), asi que dos llamadas seguidas sin
// insertar devuelven el mismo id.
function transferencia({ origen, sinpe_origen, destino, monto, fecha }) {
  return {
    _id: siguienteTxn(),
    cuenta_origen: origen,
    sinpe_origen: sinpe_origen,
    cuenta_destino: destino,
    monto_crc: NumberLong(String(monto)),
    moneda: "CRC",
    canal: "sinpe_movil",
    fecha: fecha,
    esquema_version: 1
  };
}

print("ayudas cargadas: hoyCR(hora, minuto), siguienteTxn(), transferencia({...})");
