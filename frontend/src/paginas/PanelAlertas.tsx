import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { pedir } from "../api/cliente";
import { CLAVE_SESION } from "../api/sesion";
import type { Alerta, EventoAlerta, Pagina } from "../api/tipos";
import { aplicarAlerta } from "../cola";
import { AvisoRezago } from "../componentes/AvisoRezago";
import { EncabezadoPanel } from "../componentes/EncabezadoPanel";
import { colones, etiqueta, horaCostaRica } from "../formatos";
import { useEventosAlertas } from "../tiempo-real/eventosAlertas";

export const CLAVE_COLA = ["alertas-cola"] as const;

interface Propiedades {
  alAbrirAyuda: () => void;
}

/**
 * Panel "Por alerta": la cola entera, de mayor a menor puntaje (ORDEN_PANEL).
 *
 * - Datos: GET /api/v1/alertas (el limite lo acota la API con `app.pagina_maxima`).
 * - Tiempo real: el canal JSON trae la alerta completa; se agrega o reemplaza por `_id` y
 *   se reordena en el navegador, sin pedir nada. `resincronizar` o una reconexion piden la
 *   cola de nuevo: lo que se promete es reconstruir el estado, no repetir cada evento.
 */
export function PanelAlertas({ alAbrirAyuda }: Propiedades) {
  const clienteConsultas = useQueryClient();
  const [aviso, setAviso] = useState("");

  const consulta = useQuery({
    queryKey: CLAVE_COLA,
    queryFn: () => pedir<Pagina<Alerta>>("/api/v1/alertas"),
  });

  const estadoCanal = useEventosAlertas({
    alAlerta: (evento: EventoAlerta) => {
      setAviso(`Alerta nueva o actualizada: ${evento.alerta._id}`);
      clienteConsultas.setQueryData<Pagina<Alerta>>(CLAVE_COLA, (pagina) =>
        pagina ? aplicarAlerta(pagina, evento.alerta) : pagina,
      );
    },
    alResincronizar: () => void clienteConsultas.invalidateQueries({ queryKey: CLAVE_COLA }),
    alCerrar: () => void clienteConsultas.invalidateQueries({ queryKey: CLAVE_SESION }),
  });

  const alertas = consulta.data?.elementos ?? [];
  return (
    <>
      <EncabezadoPanel
        vista="alertas"
        descripcion="Cola ordenada por puntaje (mayor primero) y luego por fecha."
        estadoCanal={estadoCanal}
        aviso={aviso}
        rotuloActualizar="Actualizar cola"
        alActualizar={() => void consulta.refetch()}
        alAbrirAyuda={alAbrirAyuda}
      />

      <AvisoRezago />

      <section className="tarjeta" aria-label="Cola de alertas">
        {consulta.isPending ? <p role="status">Cargando la cola…</p> : null}
        {consulta.isError ? (
          <div className="mensaje mensaje-error" role="alert">
            {consulta.error.message}
          </div>
        ) : null}
        {consulta.data ? (
          <div role="table" aria-label="Cola de alertas">
            <div role="rowgroup">
              <div className="cola-encabezado" role="row">
                <span role="columnheader">Alerta</span>
                <span role="columnheader">Severidad</span>
                <span role="columnheader">Puntaje</span>
                <span role="columnheader">Cliente</span>
                <span role="columnheader">Cuentas</span>
                <span role="columnheader">Monto</span>
                <span role="columnheader">Creada (hora CR)</span>
                <span role="columnheader">Estado</span>
              </div>
            </div>
            <div id="cola" role="rowgroup">
              {alertas.map((alerta) => (
                <div
                  key={alerta._id}
                  id={`alerta-${alerta._id}`}
                  role="row"
                  data-fila-atajo
                  className={`fila-alerta severidad-${alerta.severidad}`}
                  data-estado={alerta.estado}
                  data-destino={alerta.cuenta_destino}
                  data-puntaje={alerta.puntaje}
                  data-monto-crc={alerta.monto_crc}
                >
                  <span role="cell" className="alerta-id">
                    <Link to={`/detalle/${alerta._id}`}>{alerta._id}</Link>
                  </span>
                  <span role="cell" className="alerta-severidad">
                    {etiqueta(alerta.severidad)}
                  </span>
                  <span role="cell" className="alerta-puntaje">
                    {alerta.puntaje}
                  </span>
                  <span role="cell" className="alerta-cliente">
                    {alerta.cliente_nombre}
                  </span>
                  <span role="cell" className="alerta-cuentas">
                    {alerta.cuenta_origen} a {alerta.cuenta_destino}
                  </span>
                  <span role="cell" className="alerta-monto">
                    {colones(alerta.monto_crc)}
                  </span>
                  <span role="cell" className="alerta-fecha">
                    {horaCostaRica(alerta.fecha_creacion)}
                  </span>
                  <span role="cell" className="alerta-estado">
                    {etiqueta(alerta.estado)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
        {consulta.data && alertas.length === 0 ? (
          <p id="cola-vacia" className="cola-vacia">
            No hay alertas en la cola.
          </p>
        ) : null}
      </section>
    </>
  );
}
