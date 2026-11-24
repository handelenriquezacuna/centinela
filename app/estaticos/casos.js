/*
 * Vista por casos del panel (T-56): seleccion, accion en lote con confirmacion y tiempo real.
 *
 * - Seleccion: casilla por alerta y una por caso para marcar todas. El conteo se anuncia.
 * - Accion en lote: el boton abre un dialogo propio (<dialog>, con foco y Esc nativos) que
 *   dice cuantas alertas y a que estado, y pide el motivo. Al confirmar se llenan los campos
 *   ocultos del formulario del caso y HTMX lo envia a POST /panel/lote. Nada se cierra solo.
 * - Tiempo real: cada alerta que llega por SSE trae `data-destino`; se pide el fragmento de
 *   ESE caso y se reemplaza (o se agrega si es nuevo), conservando lo que el agente marco.
 */
(function () {
  "use strict";
  var casos = document.getElementById("casos");
  if (!casos) { return; }

  var dialogo = document.getElementById("dialogo-lote");
  var pendiente = null;
  var TERMINALES = { confirmada: 1, descartada: 1, escalada: 1 };

  function marcadas(formulario) {
    return Array.prototype.slice.call(formulario.querySelectorAll("input[name='alerta']:checked"));
  }

  function actualizarConteo(formulario) {
    var conteo = formulario.querySelector("[data-conteo]");
    if (conteo) { conteo.textContent = String(marcadas(formulario).length); }
    var todo = formulario.querySelector("[data-seleccionar-todo]");
    var cajas = formulario.querySelectorAll("input[name='alerta']");
    if (todo) { todo.checked = cajas.length > 0 && marcadas(formulario).length === cajas.length; }
  }

  function avisar(texto) {
    var aviso = document.getElementById("aviso-nuevas");
    if (aviso) { aviso.textContent = texto; }
  }

  casos.addEventListener("change", function (evento) {
    var formulario = evento.target.closest("form.form-lote");
    if (!formulario) { return; }
    if (evento.target.matches("[data-seleccionar-todo]")) {
      formulario.querySelectorAll("input[name='alerta']").forEach(function (c) {
        c.checked = evento.target.checked;
      });
    }
    actualizarConteo(formulario);
  });

  casos.addEventListener("click", function (evento) {
    var boton = evento.target.closest("[data-accion-lote]");
    if (!boton || !dialogo) { return; }
    var formulario = boton.closest("form.form-lote");
    var ids = marcadas(formulario).map(function (c) { return c.value; });
    if (ids.length === 0) {
      avisar("Selecciona al menos una alerta de la cuenta.");
      return;
    }
    var destino = boton.getAttribute("data-accion-lote");
    var cuenta = formulario.querySelector("input[name='cuenta']").value;
    pendiente = { formulario: formulario, destino: destino };
    document.getElementById("dialogo-lote-titulo").textContent = boton.getAttribute("data-rotulo") + ": confirmar";
    document.getElementById("dialogo-lote-texto").textContent =
      "Vas a aplicar «" + boton.getAttribute("data-rotulo") + "» a " + ids.length +
      (ids.length === 1 ? " alerta" : " alertas") + " hacia la cuenta destino " + cuenta + ". Cada una pasa por la máquina de estados; " +
      "las que no puedan pasar se informan al terminar." +
      (TERMINALES[destino] ? " Esta acción cierra las alertas y no se puede deshacer." : "");
    document.getElementById("dialogo-lote-motivo").value = "";
    dialogo.showModal();
    document.getElementById("dialogo-lote-confirmar").focus();
  });

  if (dialogo) {
    dialogo.addEventListener("close", function () {
      var cerro = dialogo.returnValue;
      dialogo.returnValue = "";
      if (cerro !== "confirmar" || !pendiente) { pendiente = null; return; }
      var formulario = pendiente.formulario;
      formulario.querySelector("input[name='destino']").value = pendiente.destino;
      formulario.querySelector("input[name='nota']").value =
        document.getElementById("dialogo-lote-motivo").value.trim();
      pendiente = null;
      formulario.requestSubmit();
    });
  }

  // --- orden y conteo de la lista -----------------------------------------------------
  function ordenar() {
    var lista = Array.prototype.slice.call(casos.querySelectorAll(":scope > .caso"));
    function clave(c) {
      var rango = c.dataset.rango;
      return rango === undefined
        ? [-Infinity, 0, 0]
        : [-Number(rango), -Number(c.dataset.puntaje), Number(c.dataset.antigua)];
    }
    lista
      .map(function (c, i) { return { c: c, i: i, k: clave(c) }; })
      .sort(function (a, b) {
        for (var n = 0; n < 3; n++) {
          if (a.k[n] !== b.k[n]) { return a.k[n] < b.k[n] ? -1 : 1; }
        }
        return a.i - b.i;
      })
      .forEach(function (par) { casos.appendChild(par.c); });
    var abiertos = casos.querySelectorAll(":scope > .caso:not(.caso-resuelto)").length;
    var resumen = document.getElementById("resumen-casos");
    if (resumen) { resumen.textContent = abiertos + " cuentas destino con alertas abiertas."; }
    var vacio = document.getElementById("casos-vacio");
    if (vacio) { vacio.hidden = casos.querySelectorAll(":scope > .caso").length > 0; }
  }

  // --- tiempo real --------------------------------------------------------------------
  var temporizadores = {};

  function refrescarCaso(cuenta) {
    clearTimeout(temporizadores[cuenta]);
    temporizadores[cuenta] = setTimeout(function () {
      fetch("/panel/casos/" + encodeURIComponent(cuenta), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.text() : ""; })
        .then(function (html) { reemplazarCaso(cuenta, html.trim()); });
    }, 250);
  }

  function reemplazarCaso(cuenta, html) {
    var actual = document.getElementById("caso-" + cuenta);
    var marcados = {};
    var foco = document.activeElement && document.activeElement.closest ? document.activeElement.closest("[id^='alerta-']") : null;
    var idFoco = foco ? foco.id : null;
    if (actual) {
      marcadas(actual).forEach(function (c) { marcados[c.value] = true; });
    }
    if (html === "") {
      if (actual && !actual.classList.contains("caso-resuelto")) { actual.remove(); }
      ordenar();
      return;
    }
    var plantilla = document.createElement("template");
    plantilla.innerHTML = html;
    var nuevo = plantilla.content.firstElementChild;
    if (!nuevo) { return; }
    if (actual) { actual.replaceWith(nuevo); } else { casos.prepend(nuevo); }
    nuevo.querySelectorAll("input[name='alerta']").forEach(function (c) {
      if (marcados[c.value]) { c.checked = true; }
    });
    var formulario = nuevo.querySelector("form.form-lote");
    if (formulario) { actualizarConteo(formulario); }
    if (window.htmx) { window.htmx.process(nuevo); }
    if (idFoco) {
      var otra = document.getElementById(idFoco);
      if (otra) { otra.tabIndex = -1; otra.focus(); }
    }
    ordenar();
    avisar(actual ? "Cuenta actualizada: " + cuenta : "Cuenta nueva: " + cuenta);
  }

  document.body.addEventListener("htmx:sseMessage", function (evento) {
    var datos = evento.detail && evento.detail.data;
    if (typeof datos !== "string") { return; }
    var plantilla = document.createElement("template");
    plantilla.innerHTML = datos;
    var fila = plantilla.content.querySelector("[data-destino]");
    if (fila && fila.dataset.destino) { refrescarCaso(fila.dataset.destino); }
  });

  // Tras un lote el servidor devuelve el caso ya actualizado: se reordena y se anuncia.
  document.body.addEventListener("htmx:afterSwap", function (evento) {
    if (evento.target && evento.target.id && evento.target.id.indexOf("caso-") === 0 ||
        (evento.detail.elt && evento.detail.elt.closest && evento.detail.elt.closest("form.form-lote"))) {
      ordenar();
      var resultado = casos.querySelector("[data-resultado-lote]");
      if (resultado) { avisar(resultado.textContent.replace(/\s+/g, " ").trim()); }
    }
  });

  casos.querySelectorAll("form.form-lote").forEach(actualizarConteo);
})();
