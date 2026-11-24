/*
 * Atajos de teclado de la cola (T-56), sin librerias.
 *
 *   j / k   bajar / subir una alerta        x       marcar la alerta (vista por caso)
 *   Enter   abrir el detalle                ?       mostrar u ocultar la ayuda
 *
 * No se activan mientras se escribe (campos de texto, areas, selects) ni con Ctrl, Alt o
 * Meta, y tampoco con un dialogo modal abierto. La fila con el foco se ve por el contorno
 * de `.fila-alerta:focus` y `.fila-caso:focus` en centinela.css.
 */
(function () {
  "use strict";
  var SELECTOR = ".fila-alerta, .fila-caso";
  var NO_TEXTO = { checkbox: 1, radio: 1, button: 1, submit: 1 };

  function escribiendo(destino) {
    if (!destino || !destino.tagName) { return false; }
    if (destino.isContentEditable) { return true; }
    var etiqueta = destino.tagName;
    if (etiqueta === "TEXTAREA" || etiqueta === "SELECT") { return true; }
    return etiqueta === "INPUT" && !NO_TEXTO[(destino.type || "text").toLowerCase()];
  }

  function filas() { return Array.prototype.slice.call(document.querySelectorAll(SELECTOR)); }

  function actual() {
    var activo = document.activeElement;
    return activo && activo.closest ? activo.closest(SELECTOR) : null;
  }

  function enfocar(fila) {
    if (!fila) { return; }
    fila.tabIndex = -1;
    fila.focus();
  }

  function mover(paso) {
    var lista = filas();
    if (lista.length === 0) { return; }
    var i = lista.indexOf(actual());
    enfocar(lista[i === -1 ? (paso > 0 ? 0 : lista.length - 1) : Math.max(0, Math.min(lista.length - 1, i + paso))]);
  }

  var ayuda = document.getElementById("ayuda-atajos");
  function alternarAyuda() {
    if (!ayuda) { return; }
    if (ayuda.open) { ayuda.close(); } else { ayuda.showModal(); }
  }
  var botonAyuda = document.getElementById("abrir-ayuda");
  if (botonAyuda) { botonAyuda.addEventListener("click", alternarAyuda); }

  document.addEventListener("keydown", function (evento) {
    if (evento.ctrlKey || evento.metaKey || evento.altKey) { return; }
    if (escribiendo(evento.target)) { return; }
    if (evento.key === "?") {
      evento.preventDefault();
      alternarAyuda();
      return;
    }
    if (document.querySelector("dialog[open]")) { return; }
    var fila = actual();
    if (evento.key === "j") { evento.preventDefault(); mover(1); }
    else if (evento.key === "k") { evento.preventDefault(); mover(-1); }
    else if (evento.key === "x" && fila) {
      var casilla = fila.querySelector("input[type='checkbox'][name='alerta']");
      if (casilla) {
        evento.preventDefault();
        casilla.checked = !casilla.checked;
        casilla.dispatchEvent(new Event("change", { bubbles: true }));
      }
    } else if (evento.key === "Enter" && fila && document.activeElement === fila) {
      var enlace = fila.querySelector("a[href^='/detalle/']");
      if (enlace) { evento.preventDefault(); window.location.href = enlace.href; }
    }
  });
})();
