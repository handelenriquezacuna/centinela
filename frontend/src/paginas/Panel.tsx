import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { ErrorApi, pedir } from "../api/cliente";
import { CLAVE_SESION } from "../api/sesion";
import type { CasoDestino, EventoAlerta, Pagina } from "../api/tipos";
import { aplicarCaso } from "../casos";
import { useAtajosDelPanel } from "../atajos";
import { AvisoRezago } from "../componentes/AvisoRezago";
import { AyudaAtajos } from "../componentes/AyudaAtajos";
import { Cargando } from "../componentes/Cargando";
import { EncabezadoPanel, type VistaPanel } from "../componentes/EncabezadoPanel";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { VistaError } from "../componentes/VistaError";
import { VistaVacia } from "../componentes/VistaVacia";
import { mensajeDeError } from "../reglas/errores";
import { useEventosAlertas } from "../tiempo-real/eventosAlertas";
import { CasoTarjeta } from "./CasoTarjeta";
import { PanelAlertas } from "./PanelAlertas";

export const CLAVE_CASOS = ["casos-destino"] as const;
/** Varias alertas de la misma cuenta en rafaga se juntan en un solo pedido del caso. */
const ESPERA_MS = 250;

async function leerCaso(cuenta: string): Promise<CasoDestino | null> {
  try {
    return await pedir<CasoDestino>(`/api/v1/casos-destino/${encodeURIComponent(cuenta)}`);
  } catch (fallo) {
    if (fallo instanceof ErrorApi && fallo.estado === 404) {
      return null; // ya no tiene alertas abiertas
    }
    throw fallo;
  }
}

const CLAVE_VISTA = "centinela_vista";
const VISTA_POR_OMISION: VistaPanel = "casos";

function esVista(valor: string | null | undefined): valor is VistaPanel {
  return valor === "casos" || valor === "alertas";
}

function vistaRecordada(): VistaPanel | null {
  try {
    const guardada = window.localStorage.getItem(CLAVE_VISTA);
    return esVista(guardada) ? guardada : null;
  } catch {
    return null;
  }
}

/**
 * El panel: las dos vistas de la cola con el conmutador "Por cuenta destino / Por alerta".
 * Por omision se agrupa por cuenta destino (D-56); `?vista=` elige y la eleccion se
 * recuerda en este navegador (la cookie del servidor es HttpOnly y no la escribe la SPA).
 * Los atajos de teclado y su ayuda son de las dos vistas.
 */
export function Panel() {
  const [parametros] = useSearchParams();
  const navegar = useNavigate();
  const ayuda = useRef<HTMLDialogElement>(null);
  const pedida = parametros.get("vista");
  const vista: VistaPanel = esVista(pedida) ? pedida : (vistaRecordada() ?? VISTA_POR_OMISION);

  useEffect(() => {
    if (esVista(pedida)) {
      try {
        window.localStorage.setItem(CLAVE_VISTA, pedida);
      } catch {
        // Sin almacenamiento la eleccion solo vale para esta visita.
      }
    }
  }, [pedida]);

  const alternarAyuda = useCallback(() => {
    const dialogo = ayuda.current;
    if (!dialogo) return;
    if (dialogo.open) dialogo.close();
    else dialogo.showModal();
  }, []);
  useAtajosDelPanel({ alternarAyuda, abrir: (ruta) => void navegar(ruta) });

  return (
    <>
      {vista === "alertas" ? <PanelAlertas alAbrirAyuda={alternarAyuda} /> : <PanelCasos alAbrirAyuda={alternarAyuda} />}
      <AyudaAtajos ref={ayuda} />
    </>
  );
}

/**
 * Panel por cuenta destino (D-56): la pantalla de referencia de la migracion (T-61).
 *
 * - Datos: GET /api/v1/casos-destino (TanStack Query).
 * - Tiempo real: el canal JSON. Cada alerta que llega pide de nuevo SOLO el caso de su
 *   cuenta y lo reemplaza, lo agrega o lo quita; `resincronizar` o una reconexion piden la
 *   lista completa. Nada de bus en memoria: lo que llega es lo que el change stream vio.
 * - Accesible: regiones `aria-live` para el estado del canal y lo que llega, tablas con
 *   encabezados y casillas con etiqueta.
 */
function PanelCasos({ alAbrirAyuda }: { alAbrirAyuda: () => void }) {
  const clienteConsultas = useQueryClient();
  const [aviso, setAviso] = useState("");
  const temporizadores = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const consulta = useQuery({
    queryKey: CLAVE_CASOS,
    queryFn: () => pedir<Pagina<CasoDestino>>("/api/v1/casos-destino"),
  });

  const refrescarCaso = useCallback(
    (cuenta: string) => {
      const previo = temporizadores.current.get(cuenta);
      if (previo !== undefined) clearTimeout(previo);
      temporizadores.current.set(
        cuenta,
        setTimeout(() => {
          temporizadores.current.delete(cuenta);
          void leerCaso(cuenta).then(
            (caso) => {
              clienteConsultas.setQueryData<Pagina<CasoDestino>>(CLAVE_CASOS, (pagina) =>
                pagina ? aplicarCaso(pagina, cuenta, caso) : pagina,
              );
            },
            () => void clienteConsultas.invalidateQueries({ queryKey: CLAVE_CASOS }),
          );
        }, ESPERA_MS),
      );
    },
    [clienteConsultas],
  );

  useEffect(() => {
    const pendientes = temporizadores.current;
    return () => pendientes.forEach((t) => clearTimeout(t));
  }, []);

  const estadoCanal = useEventosAlertas({
    alAlerta: (evento: EventoAlerta) => {
      const { alerta } = evento;
      setAviso(`Alerta nueva o actualizada: ${alerta._id} hacia la cuenta ${alerta.cuenta_destino}`);
      refrescarCaso(alerta.cuenta_destino);
    },
    alResincronizar: () => void clienteConsultas.invalidateQueries({ queryKey: CLAVE_CASOS }),
    alCerrar: () => void clienteConsultas.invalidateQueries({ queryKey: CLAVE_SESION }),
  });

  const pagina = consulta.data;
  return (
    <MarcoPagina id="panel" ancho="ancho">
      <EncabezadoPanel
        vista="casos"
        descripcion="Por cuenta destino: alertas abiertas agrupadas por la cuenta que las recibe, de mayor a menor severidad."
        estadoCanal={estadoCanal}
        aviso={aviso}
        rotuloActualizar="Actualizar cuentas"
        alActualizar={() => void consulta.refetch()}
        alAbrirAyuda={alAbrirAyuda}
      />

      <AvisoRezago />

      <section className="tarjeta" aria-label="Alertas agrupadas por cuenta destino">
        {consulta.isPending ? <Cargando texto="Cargando cuentas destino…" /> : null}
        {consulta.isError ? <VistaError error={mensajeDeError(consulta.error, "Las cuentas")} alReintentar={() => void consulta.refetch()} /> : null}
        {pagina ? (
          pagina.elementos.length === 0 ? (
            <div id="casos-vacio">
              <VistaVacia titulo="No hay cuentas destino con alertas abiertas." texto="Cuando una cuenta reciba alertas aparecerá aquí agrupada." />
            </div>
          ) : (
            <>
              <p className="subtitulo" id="resumen-casos">
                {pagina.elementos.length} de {pagina.total} cuentas destino con alertas abiertas.
              </p>
              <div id="casos">
                {pagina.elementos.map((caso) => (
                  <CasoTarjeta key={caso.cuenta_destino} caso={caso} alTerminarLote={refrescarCaso} />
                ))}
              </div>
            </>
          )
        ) : null}
      </section>
    </MarcoPagina>
  );
}
