import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useReducer, useRef, useState, type KeyboardEvent } from "react";
import { useLocation, useNavigate } from "react-router";

import { ErrorApi } from "../api/cliente";
import { CLAVE_SESION, cerrarSesion, renovarSesion } from "../api/sesion";
import type { SesionActual } from "../api/tipos";
import { hayCambiosSinGuardar, vigilarCambios } from "../cambiosSinGuardar";
import { rutaDeLogin } from "../destino";

/** Cuanto antes de vencer se avisa (T-83). */
export const AVISO_ANTES_MS = 60_000;
// setTimeout con mas de 2^31-1 ms se dispara de inmediato: se acota y se vuelve a evaluar.
const MAXIMO_TEMPORIZADOR_MS = 2_000_000_000;

/** `/login?siguiente=...&motivo=vencida`: el login dice "Tu sesion vencio". */
export function rutaDeLoginVencida(ruta: string): string {
  const base = rutaDeLogin(ruta);
  return `${base}${base.includes("?") ? "&" : "?"}motivo=vencida`;
}

/**
 * Un minuto antes de que venza la sesion, un `alertdialog` ofrece seguir trabajando
 * (renueva) o salir. Al vencer, lleva al login con el destino actual y el motivo. El foco
 * entra al boton "Seguir trabajando", se queda dentro del aviso mientras esta abierto y
 * vuelve a donde estaba al cerrarse.
 */
export function AvisoSesion({ sesion }: { sesion: SesionActual }) {
  const clienteConsultas = useQueryClient();
  const navegar = useNavigate();
  const ubicacion = useLocation();
  const [visible, setVisible] = useState(false);
  const [fallo, setFallo] = useState<string | null>(null);
  const [renovando, setRenovando] = useState(false);
  const [, repintar] = useReducer((n: number) => n + 1, 0);
  const seguir = useRef<HTMLButtonElement>(null);
  const salirBoton = useRef<HTMLButtonElement>(null);
  const foco = useRef<HTMLElement | null>(null);
  const destino = useRef(ubicacion.pathname + ubicacion.search);
  const rutaActual = ubicacion.pathname + ubicacion.search;
  useEffect(() => {
    destino.current = rutaActual;
  }, [rutaActual]);

  useEffect(() => vigilarCambios(), []);

  useEffect(() => {
    const expira = Date.parse(sesion.expira);
    let temporizador: ReturnType<typeof setTimeout> | undefined;
    function evaluar() {
      const restante = expira - Date.now();
      if (restante <= 0) {
        setVisible(false);
        // Primero la sesion en cache, para que el login no rebote a quien ya no la tiene.
        clienteConsultas.setQueryData(CLAVE_SESION, null);
        navegar(rutaDeLoginVencida(destino.current), { replace: true });
        return;
      }
      const dentroDelAviso = restante <= AVISO_ANTES_MS;
      setVisible(dentroDelAviso);
      temporizador = setTimeout(evaluar, Math.min(dentroDelAviso ? restante : restante - AVISO_ANTES_MS, MAXIMO_TEMPORIZADOR_MS));
    }
    evaluar();
    return () => clearTimeout(temporizador);
  }, [sesion.expira, clienteConsultas, navegar]);

  // Foco: al abrirse entra al aviso; al cerrarse vuelve a donde estaba.
  useEffect(() => {
    if (visible) {
      foco.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      seguir.current?.focus();
      document.addEventListener("input", repintar, true);
      return () => document.removeEventListener("input", repintar, true);
    }
    foco.current?.focus();
    foco.current = null;
    return undefined;
  }, [visible]);

  async function continuar() {
    setRenovando(true);
    setFallo(null);
    try {
      const nueva = await renovarSesion();
      clienteConsultas.setQueryData<SesionActual | null>(CLAVE_SESION, (actual) => (actual ? { ...actual, expira: nueva.expira } : actual));
    } catch (error) {
      if (error instanceof ErrorApi && error.estado === 401) {
        clienteConsultas.setQueryData(CLAVE_SESION, null);
        navegar(rutaDeLoginVencida(destino.current), { replace: true });
      } else {
        setFallo("No se pudo renovar la sesión. Intenta de nuevo.");
      }
    } finally {
      setRenovando(false);
    }
  }

  async function salir() {
    try {
      await cerrarSesion();
    } finally {
      clienteConsultas.clear();
      navegar("/login", { replace: true });
    }
  }

  function teclado(evento: KeyboardEvent<HTMLDivElement>) {
    if (evento.key !== "Tab") return;
    const primero = seguir.current;
    const ultimo = salirBoton.current;
    if (!primero || !ultimo) return;
    if (evento.shiftKey && document.activeElement === primero) {
      evento.preventDefault();
      ultimo.focus();
    } else if (!evento.shiftKey && document.activeElement === ultimo) {
      evento.preventDefault();
      primero.focus();
    }
  }

  if (!visible) return null;
  const conCambios = hayCambiosSinGuardar();
  return (
    <div className="aviso-sesion" role="alertdialog" aria-modal="true" aria-labelledby="aviso-sesion-titulo" aria-describedby="aviso-sesion-texto" onKeyDown={teclado}>
      <h2 id="aviso-sesion-titulo">Tu sesión vence en 1 minuto</h2>
      <p id="aviso-sesion-texto">
        {conCambios
          ? "Tienes texto escrito sin enviar. Si la sesión vence, se pierde: pulsa Seguir trabajando para conservarlo."
          : "Pulsa Seguir trabajando para mantener la sesión abierta."}
      </p>
      {fallo ? (
        <p className="mensaje mensaje-error" role="alert">
          {fallo}
        </p>
      ) : null}
      <div className="grupo-botones">
        <button type="button" ref={seguir} className="boton boton-primario" disabled={renovando} onClick={() => void continuar()}>
          Seguir trabajando
        </button>
        <button type="button" ref={salirBoton} className="boton boton-secundario" onClick={() => void salir()}>
          Salir
        </button>
      </div>
    </div>
  );
}
