// ============================================================
// T-76 / tema 7.3 · Procedimiento: map reduce frente a aggregation
//
// Que hace: calcula LO MISMO de dos maneras, para comparar. El calculo: monto total y
// cantidad de alertas por cuenta destino (el monto y la cuenta son los de la transaccion
// que origino cada alerta; `alertas.monto_crc` y `alertas.cuenta_destino`).
//   1. db.alertas.mapReduce(map, reduce, {out: {inline: 1}})  -- el modelo de MongoDB
//      anterior a 5.0: una funcion JavaScript por documento y otra que combina.
//   2. db.alertas.aggregate([{$group}, {$sort}])               -- el que usa el proyecto.
// Imprime los dos resultados, comprueba que coinciden y termina con la nota de por que el
// proyecto usa aggregation. Con `out: {inline: 1}` mapReduce no escribe nada.
//
// Entradas (variables globales opcionales, se pasan con --eval):
//   BASE_CENTINELA  base a usar. Por omision "antifraude".
//
// Escribe: nada (solo lee alertas). Termina con error si los dos resultados difieren.
//
// Como correrlo:
//   mongosh "<uri>" --quiet --file scripts/procedimientos/05-map-reduce-comparativo.js
//   python tareas.py procedimientos 05-map-reduce-comparativo
//
// Como verificarlo: la ultima linea de resultados dice "coinciden: SI". La prueba de
// integracion lo comprueba con una base propia.
//
// Permisos: centinela_app o centinela_analista alcanzan (find; mapReduce inline lo pide).
// ============================================================

const NOMBRE_BASE = typeof BASE_CENTINELA === "string" ? BASE_CENTINELA : "antifraude";
const base = db.getSiblingDB(NOMBRE_BASE);

print("== 05-map-reduce-comparativo (base " + NOMBRE_BASE + ")");

// 1. mapReduce. `emit` agrupa por cuenta destino; `reduce` suma monto y cantidad, y tiene que
// devolver la misma forma que emite el map (puede correr varias veces sobre resultados parciales).
const viaMapReduce = {};
let mapReduceDisponible = true;
try {
  const resultado = base.alertas.mapReduce(
    function () {
      emit(this.cuenta_destino, { monto_crc: this.monto_crc, cantidad: 1 });
    },
    function (cuenta, valores) {
      const acumulado = { monto_crc: 0, cantidad: 0 };
      valores.forEach(function (v) {
        acumulado.monto_crc += Number(v.monto_crc);
        acumulado.cantidad += v.cantidad;
      });
      return acumulado;
    },
    { out: { inline: 1 } }
  );
  if (resultado.ok !== 1) throw new Error(tojson(resultado));
  resultado.results.forEach((fila) => {
    viaMapReduce[fila._id] = { monto_crc: Number(fila.value.monto_crc), cantidad: Number(fila.value.cantidad) };
  });
} catch (e) {
  mapReduceDisponible = false;
  print("mapReduce no se pudo ejecutar en este servidor: " + e.message);
}

// 2. aggregate, con $group. Un solo operador declarativo, sin JavaScript en el servidor.
const viaAggregate = {};
base.alertas.aggregate([
  { $group: { _id: "$cuenta_destino", monto_crc: { $sum: "$monto_crc" }, cantidad: { $sum: 1 } } },
  { $sort: { _id: 1 } },
]).forEach((fila) => {
  viaAggregate[fila._id] = { monto_crc: Number(fila.monto_crc), cantidad: Number(fila.cantidad) };
});

function imprimir(titulo, datos) {
  print(titulo);
  Object.keys(datos).sort().forEach((cuenta) => {
    print("  " + cuenta + "  monto_crc=" + String(datos[cuenta].monto_crc).padStart(9) + "  alertas=" + datos[cuenta].cantidad);
  });
}
imprimir("mapReduce (out inline): " + Object.keys(viaMapReduce).length + " cuenta(s) destino", viaMapReduce);
imprimir("aggregate ($group): " + Object.keys(viaAggregate).length + " cuenta(s) destino", viaAggregate);

const llaves = Object.keys(viaAggregate).sort();
const coinciden = mapReduceDisponible &&
  JSON.stringify(Object.keys(viaMapReduce).sort()) === JSON.stringify(llaves) &&
  llaves.every((k) => viaMapReduce[k].monto_crc === viaAggregate[k].monto_crc && viaMapReduce[k].cantidad === viaAggregate[k].cantidad);
print("coinciden: " + (coinciden ? "SI" : "NO"));

print("");
print("Nota: mapReduce esta obsoleto (deprecated) desde MongoDB 5.0 y la documentacion de");
print("MongoDB 8 pide reemplazarlo con aggregation. Corre JavaScript dentro del servidor por");
print("cada documento, es mas lento y mas dificil de optimizar que un pipeline, y todo lo que");
print("hace se expresa con $group, $merge o $accumulator. Por eso Centinela usa aggregation en");
print("produccion (indicadores, perfiles, rasgos de grafo, efectividad); este script existe");
print("solo para mostrar la comparacion.");

if (!coinciden) throw new Error("mapReduce y aggregate no coinciden");
