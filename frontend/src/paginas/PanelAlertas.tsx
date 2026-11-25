import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { pedir } from "../api/cliente";
import { CLAVE_SESION } from "../api/sesion";
import type { Alerta, EventoAlerta, Pagina } from "../api/tipos";
import { aplicarAlerta } from "../cola";
import { AvisoRezago } from "../componentes/AvisoRezago";
import { EncabezadoPanel } from "../componentes/EncabezadoPanel";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { Insignia } from "../componentes/Insignia";
import { MontoColones } from "../componentes/MontoColones";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { VistaVacia } from "../componentes/VistaVacia";
import { mensajeDeError } from "../reglas/errores";
import { useEventosAlertas } from "../tiempo-real/eventosAlertas";

export const CLAVE_COLA = ["alertas-cola"] as const;

const COLUMNAS: Columna<Alerta>[] = [
  { clave: "id", titulo: "Alerta", celda: (a) => <Link to={`/detalle/${a._id}`}>{a._id}</Link>, clase: "id-corto" },
  { clave: "severidad", titulo: "Severidad", celda: (a) => <Insignia tipo="severidad" valor={a.severidad} /> },
  { clave: "puntaje", titulo: "Puntaje", celda: (a) => a.puntaje },
  { clave: "cliente", titulo: "Cliente", celda: (a) => a.cliente_nombre },
  { clave: "cuentas", titulo: "Cuentas", celda: (a) => `${a.cuenta_origen} a ${a.cuenta_destino}`, clase: "id-corto" },
  { clave: "monto", titulo: "Monto", celda: (a) => <MontoColones valor={a.monto_crc} /> },
  { clave: "creada", titulo: "Creada (hora CR)", celda: (a) => <FechaCR valor={a.fecha_creacion} /> },
  { clave: "estado", titulo: "Estado", celda: (a) => <Insignia tipo="estado" valor={a.estado} /> },
];

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
    <MarcoPagina id="panel" ancho="ancho">
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
        <TablaDatos
          leyenda="Cola de alertas"
          columnas={COLUMNAS}
          filas={alertas}
          clave={(alerta) => alerta._id}
          estado={consulta.isPending ? "cargando" : consulta.isError ? "error" : "listo"}
          textoCargando="Cargando la cola…"
          error={consulta.isError ? mensajeDeError(consulta.error, "La cola") : undefined}
          alReintentar={() => void consulta.refetch()}
          idCuerpo="cola"
          atributosFila={(alerta) => ({
            id: `alerta-${alerta._id}`,
            className: `fila-cola severidad-${alerta.severidad}`,
            "data-fila-atajo": "",
            "data-estado": alerta.estado,
            "data-destino": alerta.cuenta_destino,
            "data-puntaje": alerta.puntaje,
            "data-monto-crc": alerta.monto_crc,
          })}
          vacia={<VistaVacia titulo="No hay alertas en la cola." texto="Cuando el detector genere una alerta aparecerá aquí sin recargar." />}
        />
      </section>
    </MarcoPagina>
  );
}
