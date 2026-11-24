import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorApi, pedir } from "../api/cliente";
import { CLAVE_SESION } from "../api/sesion";
import type { CasoDestino, EventoAlerta, Pagina } from "../api/tipos";
import { aplicarCaso } from "../casos";
import { AvisoRezago } from "../componentes/AvisoRezago";
import { useEventosAlertas } from "../tiempo-real/eventosAlertas";
import { CasoTarjeta } from "./CasoTarjeta";

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
export function Panel() {
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
    <>
      <div className="titulo-pantalla">
        <div>
          <h1>Panel de alertas</h1>
          <p className="subtitulo">
            Por cuenta destino: alertas abiertas agrupadas por la cuenta que las recibe, de mayor a menor severidad.{" "}
            <span className="estado-canal">
              Canal:{" "}
              <strong id="estado-canal" aria-live="polite">
                {estadoCanal}
              </strong>
            </span>
          </p>
          <p id="aviso-nuevas" className="subtitulo" aria-live="polite">
            {aviso}
          </p>
        </div>
        <div className="grupo-botones">
          <button type="button" className="boton boton-secundario" onClick={() => void consulta.refetch()}>
            Actualizar cuentas
          </button>
        </div>
      </div>

      <AvisoRezago />

      <section className="tarjeta" aria-label="Alertas agrupadas por cuenta destino">
        {consulta.isPending ? <p role="status">Cargando cuentas destino…</p> : null}
        {consulta.isError ? (
          <div className="mensaje mensaje-error" role="alert">
            {consulta.error.message}
          </div>
        ) : null}
        {pagina ? (
          <>
            <p className="subtitulo" id="resumen-casos">
              {pagina.elementos.length} de {pagina.total} cuentas destino con alertas abiertas.
            </p>
            <div id="casos">
              {pagina.elementos.map((caso) => (
                <CasoTarjeta key={caso.cuenta_destino} caso={caso} alTerminarLote={refrescarCaso} />
              ))}
            </div>
            {pagina.elementos.length === 0 ? (
              <p id="casos-vacio" className="cola-vacia">
                No hay cuentas destino con alertas abiertas.
              </p>
            ) : null}
          </>
        ) : null}
      </section>
    </>
  );
}
