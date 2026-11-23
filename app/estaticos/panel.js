/*
 * Cola del panel (H-17): deduplica y ordena despues de cada insercion SSE.
 *
 * El canal manda una fila por cambio de la alerta. Una alerta que cambio de estado
 * llega con el mismo id que una fila ya pintada: se conserva la nueva y se quita la
 * vieja. Luego se ordena por severidad (critica primero); el sort es estable y las
 * filas nuevas entran arriba, asi que dentro de una severidad lo mas reciente queda
 * primero. No hay nada que dependa de la forma de `reglas_disparadas`.
 *
 * Cada fila ya trae su enlace al detalle (lo pinta el servidor): sin JavaScript la fila
 * entera se puede abrir. Aca solo se anuncia lo que llega, en una region aria-live.
 */
(function () {
  "use strict";
  var RANGO = { CRITICA: 0, ALTA: 1, MEDIA: 2 };

  function rango(fila) {
    var clase = Array.prototype.find.call(fila.classList, function (c) {
      return c.indexOf("severidad-") === 0;
    });
    var valor = clase ? RANGO[clase.slice("severidad-".length)] : undefined;
    return valor === undefined ? 9 : valor;
  }

  // Una fila sin `data-vista` es nueva para esta pantalla: llego por SSE o por una cola recargada.
  function anunciar(cola) {
    var nuevas = Array.prototype.filter.call(cola.querySelectorAll(".fila-alerta"), function (f) {
      return !f.dataset.vista;
    });
    var aviso = document.getElementById("aviso-nuevas");
    if (aviso && nuevas.length > 0) {
      aviso.textContent = nuevas.length === 1
        ? "Alerta nueva o actualizada: " + nuevas[0].id.replace("alerta-", "")
        : nuevas.length + " alertas nuevas o actualizadas";
    }
  }

  function marcar(cola) {
    cola.querySelectorAll(".fila-alerta").forEach(function (f) { f.dataset.vista = "1"; });
  }

  function ordenar(cola) {
    var vistos = {};
    var filas = Array.prototype.slice.call(cola.querySelectorAll(".fila-alerta"));
    filas.forEach(function (fila) {
      if (vistos[fila.id]) {
        fila.remove();
      } else {
        vistos[fila.id] = true;
      }
    });
    var vigentes = Array.prototype.slice.call(cola.querySelectorAll(".fila-alerta"));
    vigentes
      .map(function (fila, i) { return { fila: fila, i: i }; })
      .sort(function (a, b) { return rango(a.fila) - rango(b.fila) || a.i - b.i; })
      .forEach(function (par) { cola.appendChild(par.fila); });
    var vacio = document.getElementById("cola-vacia");
    if (vacio) { vacio.hidden = vigentes.length > 0; }
    marcar(cola);
  }

  document.body.addEventListener("htmx:afterSwap", function (evento) {
    var cola = document.getElementById("cola");
    if (cola && (evento.target === cola || cola.contains(evento.target))) {
      anunciar(cola);
      ordenar(cola);
    }
  });
  var colaInicial = document.getElementById("cola");
  if (colaInicial) { marcar(colaInicial); }
  document.body.addEventListener("htmx:sseOpen", function () {
    var estado = document.getElementById("estado-canal");
    if (estado) { estado.textContent = "en vivo"; }
  });
  document.body.addEventListener("htmx:sseError", function () {
    var estado = document.getElementById("estado-canal");
    if (estado) { estado.textContent = "reconectando"; }
  });
})();
