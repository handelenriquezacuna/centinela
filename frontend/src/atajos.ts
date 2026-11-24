/*
 * Atajos de teclado del panel, con la misma regla que `app/estaticos/atajos.js`:
 *
 *   j / k   bajar / subir una fila       x       marcar la fila (vista por cuenta destino)
 *   Enter   abrir el detalle             ?       mostrar u ocultar la ayuda
 *
 * No se activan mientras se escribe (texto, areas, selects) ni con Ctrl, Alt o Meta, y
 * tampoco con un dialogo modal abierto (salvo `?`, que lo cierra). Las filas son las que
 * marcan `data-fila-atajo`; la que tiene el foco se ve por su contorno (centinela.css).
 */
import { useEffect, useRef } from "react";

export const SELECTOR_FILAS = "[data-fila-atajo]";
const NO_TEXTO = new Set(["checkbox", "radio", "button", "submit"]);

export function estaEscribiendo(destino: EventTarget | null): boolean {
  if (!(destino instanceof HTMLElement)) return false;
  if (destino.isContentEditable) return true;
  if (destino instanceof HTMLTextAreaElement || destino instanceof HTMLSelectElement) return true;
  return destino instanceof HTMLInputElement && !NO_TEXTO.has((destino.type || "text").toLowerCase());
}

function filas(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>(SELECTOR_FILAS)];
}

function filaActual(): HTMLElement | null {
  const activo = document.activeElement;
  return activo instanceof HTMLElement ? activo.closest<HTMLElement>(SELECTOR_FILAS) : null;
}

function mover(paso: 1 | -1): void {
  const lista = filas();
  if (lista.length === 0) return;
  const i = lista.indexOf(filaActual() as HTMLElement);
  const destino = lista[i === -1 ? (paso > 0 ? 0 : lista.length - 1) : Math.max(0, Math.min(lista.length - 1, i + paso))];
  if (destino) {
    destino.tabIndex = -1;
    destino.focus();
  }
}

interface Acciones {
  alternarAyuda: () => void;
  abrir: (ruta: string) => void;
}

export function useAtajosDelPanel(acciones: Acciones): void {
  const actuales = useRef(acciones);
  useEffect(() => {
    actuales.current = acciones;
  });

  useEffect(() => {
    function alTeclear(evento: KeyboardEvent) {
      if (evento.ctrlKey || evento.metaKey || evento.altKey) return;
      if (estaEscribiendo(evento.target)) return;
      if (evento.key === "?") {
        evento.preventDefault();
        actuales.current.alternarAyuda();
        return;
      }
      if (document.querySelector("dialog[open]")) return;
      const fila = filaActual();
      if (evento.key === "j") {
        evento.preventDefault();
        mover(1);
      } else if (evento.key === "k") {
        evento.preventDefault();
        mover(-1);
      } else if (evento.key === "x" && fila) {
        const casilla = fila.querySelector<HTMLInputElement>("input[type='checkbox']");
        if (casilla) {
          evento.preventDefault();
          casilla.click();
        }
      } else if (evento.key === "Enter" && fila && document.activeElement === fila) {
        const enlace = fila.querySelector<HTMLAnchorElement>("a[href^='/detalle/']");
        const ruta = enlace?.getAttribute("href");
        if (ruta) {
          evento.preventDefault();
          actuales.current.abrir(ruta);
        }
      }
    }
    document.addEventListener("keydown", alTeclear);
    return () => document.removeEventListener("keydown", alTeclear);
  }, []);
}
