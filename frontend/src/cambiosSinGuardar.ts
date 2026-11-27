/*
 * Detecta si hay algo escrito que se perderia al vencer la sesion (T-83): la nota de una
 * decision, el motivo de una mula, los campos de una regla. No hace falta que cada
 * formulario se registre: se escuchan los `input` de todo el documento y se recuerdan los
 * campos de formulario que la persona toco y que siguen en pantalla con algo escrito.
 *
 * No cuentan los campos de contrasena ni los de busqueda (los filtros no son trabajo
 * perdido). Un formulario que se envia deja de contar (`submit`).
 */

const tocados = new Set<HTMLInputElement | HTMLTextAreaElement>();
const TIPOS_IGNORADOS = new Set(["password", "search", "hidden", "button", "submit", "checkbox", "radio"]);

function esCampoDeTrabajo(el: EventTarget | null): el is HTMLInputElement | HTMLTextAreaElement {
  if (!(el instanceof HTMLInputElement) && !(el instanceof HTMLTextAreaElement)) return false;
  if (el instanceof HTMLInputElement && TIPOS_IGNORADOS.has(el.type)) return false;
  const formulario = el.closest("form");
  return formulario !== null && formulario.getAttribute("role") !== "search";
}

function alEscribir(evento: Event) {
  if (esCampoDeTrabajo(evento.target)) tocados.add(evento.target);
}

function alEnviar(evento: Event) {
  if (!(evento.target instanceof HTMLFormElement)) return;
  for (const campo of [...tocados]) {
    if (evento.target.contains(campo)) tocados.delete(campo);
  }
}

/** Empieza a vigilar. Devuelve como dejar de hacerlo. */
export function vigilarCambios(): () => void {
  document.addEventListener("input", alEscribir, true);
  document.addEventListener("submit", alEnviar, true);
  return () => {
    document.removeEventListener("input", alEscribir, true);
    document.removeEventListener("submit", alEnviar, true);
    tocados.clear();
  };
}

export function hayCambiosSinGuardar(): boolean {
  for (const campo of [...tocados]) {
    if (!campo.isConnected) {
      tocados.delete(campo);
    } else if (campo.value.trim() !== "") {
      return true;
    }
  }
  return false;
}
