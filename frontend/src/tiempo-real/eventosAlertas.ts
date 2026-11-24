/*
 * El canal de alertas en JSON (GET /api/v1/alertas/eventos), con EventSource.
 *
 * Contrato (docs/06-plataforma.md, "Canal SSE JSON"): evento `alerta` con
 * `{formato, operacion, alerta}` y el token de reanudacion como `id`; evento
 * `resincronizar` con `{formato, motivo}`. El navegador reconecta solo y manda el
 * `Last-Event-ID`; el servidor reanuda desde ahi. Esta capa:
 *
 * - entrega cada alerta a `alAlerta` (la pantalla decide que pedir de nuevo);
 * - ante `resincronizar` o una reconexion, llama a `alResincronizar`: la promesa es
 *   reconstruir el estado de la lista, no repetir cada evento;
 * - publica el estado del canal para mostrarlo (`conectando`, `en vivo`,
 *   `reconectando`, `desconectado`). Un canal cerrado del todo (por ejemplo, un 401)
 *   avisa con `alCerrar` para que la SPA revise la sesion.
 */
import { useEffect, useRef, useState } from "react";

import type { EventoAlerta, EventoResincronizar } from "../api/tipos";

export const RUTA_EVENTOS = "/api/v1/alertas/eventos";
export const FORMATO_EVENTO = 1;

export type EstadoCanal = "conectando" | "en vivo" | "reconectando" | "desconectado";

export interface ManejadoresCanal {
  alAlerta: (evento: EventoAlerta) => void;
  alResincronizar: (motivo: string) => void;
  alCerrar?: () => void;
}

function leer<T extends { formato: number }>(datos: unknown): T | null {
  if (typeof datos !== "string") {
    return null;
  }
  try {
    const cuerpo = JSON.parse(datos) as T;
    return cuerpo.formato === FORMATO_EVENTO ? cuerpo : null;
  } catch {
    return null;
  }
}

export function useEventosAlertas(manejadores: ManejadoresCanal): EstadoCanal {
  const [estado, setEstado] = useState<EstadoCanal>("conectando");
  // Los manejadores cambian en cada render; el canal se abre una sola vez.
  const actuales = useRef(manejadores);
  useEffect(() => {
    actuales.current = manejadores;
  });

  useEffect(() => {
    const fuente = new EventSource(RUTA_EVENTOS, { withCredentials: true });
    let huboError = false;

    fuente.onopen = () => {
      setEstado("en vivo");
      if (huboError) {
        huboError = false;
        actuales.current.alResincronizar("reconexion");
      }
    };
    fuente.onerror = () => {
      huboError = true;
      if (fuente.readyState === EventSource.CLOSED) {
        setEstado("desconectado");
        actuales.current.alCerrar?.();
      } else {
        setEstado("reconectando");
      }
    };
    fuente.addEventListener("alerta", (mensaje) => {
      const cuerpo = leer<EventoAlerta>((mensaje as MessageEvent).data);
      if (cuerpo !== null) {
        actuales.current.alAlerta(cuerpo);
      }
    });
    fuente.addEventListener("resincronizar", (mensaje) => {
      const cuerpo = leer<EventoResincronizar>((mensaje as MessageEvent).data);
      actuales.current.alResincronizar(cuerpo?.motivo ?? "resincronizar");
    });

    return () => {
      fuente.close();
    };
  }, []);

  return estado;
}
