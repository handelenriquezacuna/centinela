// ============================================================
// T-76 / H-10 · Procedimiento: reporte de efectividad del motor
//
// Que hace: compara las alertas que levanto el motor contra la etiqueta oculta
// `transacciones.simulacion` que escribe el generador, e imprime una tabla con
// exhaustividad (recall), precision y F1, en total y por patron de fraude. Es el mismo
// calculo que GET /api/v1/efectividad (app/deteccion/efectividad.py y
// app/repos/efectividad.py); F1 se agrega aqui.
//
// Definiciones (la unidad es la transaccion etiquetada; una alerta cuenta sin importar su
// estado):
//   detectados (VP)  fraude con alerta        perdidos (FN)  fraude sin alerta
//   falsos positivos legitima con alerta      verdaderos negativos  legitima sin alerta
//   precision     = VP / (VP + FP)            exhaustividad = VP / (VP + FN)
//   F1            = 2 * VP / (2 * VP + FP + FN), la media armonica de las dos
// Con denominador cero la metrica es "n/d", no 0 ni 1. Por patron solo hay exhaustividad.
// Las alertas de transacciones sin etiqueta (el demo) se informan aparte.
//
// Entradas (variables globales opcionales, se pasan con --eval):
//   BASE_CENTINELA  base a usar. Por omision "antifraude".
//
// Escribe: nada (solo lee transacciones y alertas). Es reejecutable sin efectos.
//
// Como correrlo:
//   mongosh "<uri>" --quiet --file scripts/procedimientos/04-reporte-efectividad.js
//   python tareas.py procedimientos 04-reporte-efectividad
//
// Como verificarlo: las cifras coinciden con las de GET /api/v1/efectividad; la prueba
// de equivalencia compara esta tabla con la que imprime
// `python -m app.deteccion.efectividad`.
//
// Permisos: centinela_app o centinela_analista alcanzan (solo find).
// ============================================================

const NOMBRE_BASE = typeof BASE_CENTINELA === "string" ? BASE_CENTINELA : "antifraude";
const base = db.getSiblingDB(NOMBRE_BASE);

const SIN_PATRON = "sin_patron";

const grupos = base.transacciones.aggregate([
  { $match: { simulacion: { $exists: true } } },
  {
    $lookup: {
      from: "alertas",
      localField: "_id",
      foreignField: "transaccion_id",
      pipeline: [{ $project: { _id: 1 } }],
      as: "alerta",
    },
  },
  {
    $group: {
      _id: { fraude: "$simulacion.fraude", patron: "$simulacion.patron", episodio: "$simulacion.episodio" },
      transacciones: { $sum: 1 },
      alertadas: { $sum: { $cond: [{ $gt: [{ $size: "$alerta" }, 0] }, 1, 0] } },
    },
  },
]).toArray();
// El total se lee despues del cruce: las alertas no se borran, asi que solo puede crecer.
const alertasTotales = base.alertas.countDocuments({});

const filas = grupos.map((g) => ({
  fraude: Boolean(g._id.fraude),
  patron: g._id.patron === undefined ? null : g._id.patron,
  episodio: g._id.episodio === undefined ? null : g._id.episodio,
  transacciones: Number(g.transacciones),
  alertadas: Number(g.alertadas),
}));

const suma = (lista, campo) => lista.reduce((acc, f) => acc + f[campo], 0);
const fraude = filas.filter((f) => f.fraude);
const legitimas = filas.filter((f) => !f.fraude);
const detectados = suma(fraude, "alertadas");
const fraudes = suma(fraude, "transacciones");
const falsosPositivos = suma(legitimas, "alertadas");
const totalLegitimas = suma(legitimas, "transacciones");
const alertasEtiquetadas = detectados + falsosPositivos;
if (alertasTotales < alertasEtiquetadas) {
  throw new Error(alertasTotales + " alertas en total pero " + alertasEtiquetadas +
    " sobre transacciones etiquetadas: los conteos se leyeron de momentos distintos");
}
const perdidos = fraudes - detectados;

function razon(numerador, denominador) {
  return denominador === 0 ? null : Math.round((numerador / denominador) * 10000) / 10000;
}
const porcentaje = (valor) => (valor === null ? "n/d" : (100 * valor).toFixed(1) + " %");
const izq = (texto, ancho) => String(texto).padEnd(ancho);
const der = (texto, ancho) => String(texto).padStart(ancho);

const porPatron = {};
fraude.forEach((f) => {
  const clave = f.patron === null ? SIN_PATRON : f.patron;
  (porPatron[clave] = porPatron[clave] || []).push(f);
});

const encabezado = izq("patron", 30) + der("fraudes", 9) + der("detect.", 9) + der("perdidos", 10) +
  der("exhaust.", 10) + der("episodios", 11);
const lineas = [
  "Efectividad del motor (contra la etiqueta oculta `simulacion`)",
  "",
  "transacciones etiquetadas: " + (fraudes + totalLegitimas) + "  (fraude " + fraudes + ", legitimas " + totalLegitimas + ")",
  "alertas: " + alertasTotales + "  (sin etiqueta: " + (alertasTotales - alertasEtiquetadas) + ")",
  "detectados: " + detectados + "  perdidos: " + perdidos + "  falsos positivos: " + falsosPositivos +
    "  verdaderos negativos: " + (totalLegitimas - falsosPositivos),
  "precision: " + porcentaje(razon(detectados, alertasEtiquetadas)) + "  exhaustividad: " +
    porcentaje(razon(detectados, fraudes)),
  "F1: " + porcentaje(razon(2 * detectados, 2 * detectados + falsosPositivos + perdidos)),
  "",
  encabezado,
  "-".repeat(encabezado.length),
];
Object.keys(porPatron).sort().forEach((patron) => {
  const grupo = porPatron[patron];
  const delPatron = suma(grupo, "transacciones");
  const atrapados = suma(grupo, "alertadas");
  const episodios = new Set(grupo.map((f) => f.episodio));
  const conAlerta = new Set(grupo.filter((f) => f.alertadas > 0).map((f) => f.episodio));
  lineas.push(
    izq(patron, 30) + der(delPatron, 9) + der(atrapados, 9) + der(delPatron - atrapados, 10) +
    der(porcentaje(razon(atrapados, delPatron)), 10) + der(conAlerta.size + "/" + episodios.size, 11)
  );
});
lineas.push("-".repeat(encabezado.length));
lineas.push(izq("total", 30) + der(fraudes, 9) + der(detectados, 9) + der(perdidos, 10) +
  der(porcentaje(razon(detectados, fraudes)), 10));

print("== 04-reporte-efectividad (base " + NOMBRE_BASE + ")");
lineas.forEach((l) => print(l));
