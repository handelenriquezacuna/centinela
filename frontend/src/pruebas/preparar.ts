/*
 * Preparacion de Vitest: los comparadores de jest-dom, limpieza entre pruebas y lo que
 * jsdom no trae (EventSource, y showModal/close de <dialog> segun la version).
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

if (typeof HTMLDialogElement !== "undefined") {
  const prototipo = HTMLDialogElement.prototype as HTMLDialogElement & Record<string, unknown>;
  if (typeof prototipo.showModal !== "function") {
    prototipo.showModal = function showModal(this: HTMLDialogElement) {
      this.setAttribute("open", "");
    };
  }
  if (typeof prototipo.close !== "function") {
    prototipo.close = function close(this: HTMLDialogElement, valor?: string) {
      this.removeAttribute("open");
      if (valor !== undefined) this.returnValue = valor;
      this.dispatchEvent(new Event("close"));
    };
  }
}
