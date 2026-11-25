import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";

import { pedir } from "../api/cliente";
import type { ElementoNotificacion, Pagina } from "../api/tipos";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { Insignia } from "../componentes/Insignia";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { MontoColones } from "../componentes/MontoColones";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { VistaVacia } from "../componentes/VistaVacia";
import { etiqueta } from "../formatos";
import { mensajeDeError } from "../reglas/errores";

const LIMITE = 50;
const ESTADOS = ["pendiente", "enviado", "fallido"] as const;

function leerEstado(crudo: string | null): string | undefined {
  return ESTADOS.find((estado) => estado === crudo);
}

const COLUMNAS: Columna<ElementoNotificacion>[] = [
  { clave: "fecha", titulo: "Fecha (hora CR)", celda: (n) => <FechaCR valor={n.fecha} /> },
  { clave: "alerta", titulo: "Alerta", celda: (n) => <Link to={`/detalle/${n.alerta_id}`}>{n.alerta_id}</Link> },
  { clave: "cliente", titulo: "Cliente", celda: (n) => n.cliente_nombre ?? "" },
  { clave: "monto", titulo: "Monto", clase: "numerica", celda: (n) => <MontoColones valor={n.monto_crc} /> },
  {
    clave: "estado",
    titulo: "Estado del envío",
    celda: (n) => (
      <>
        <Insignia tipo="estado" valor={`envio-${n.estado_envio}`} texto={etiqueta(n.estado_envio)} />
        {n.detalle_error ? <span className="nota-tecnica"> ({n.detalle_error})</span> : null}
      </>
    ),
  },
  { clave: "intentos", titulo: "Intentos", clase: "numerica", celda: (n) => n.intentos ?? "" },
];

/**
 * Notificaciones del supervisor (H-11): los correos de alertas criticas que envio D2,
 * las mas recientes primero. Lee GET /api/v1/notificaciones. El envio es al menos una vez:
 * un correo puede llegar repetido si el servicio se reinicio, y la pantalla lo dice.
 */
export function Notificaciones() {
  const [parametros, fijarParametros] = useSearchParams();
  const estado = leerEstado(parametros.get("estado"));
  const desplazamiento = Math.max(0, Number(parametros.get("desplazamiento")) || 0);
  const consulta = useQuery({
    queryKey: ["notificaciones", estado, desplazamiento],
    queryFn: () =>
      pedir<Pagina<ElementoNotificacion>>("/api/v1/notificaciones", {
        consulta: { limite: LIMITE, desplazamiento, estado_envio: estado },
      }),
    // Un correo nuevo aparece sin recargar: la lista es corta y la consulta es barata.
    refetchInterval: 10_000,
  });
  const pagina = consulta.data;

  return (
    <MarcoPagina id="notificaciones">
      <EncabezadoPagina
        id="notificaciones"
        titulo="Notificaciones"
        descripcion="Correos de alertas críticas que envió el sistema, los más recientes primero. El envío es al menos una vez: un correo puede llegar repetido si el servicio se reinició."
      />
      <nav className="periodos" aria-label="Estado del envío">
        <Link to="/notificaciones" aria-current={estado === undefined ? "true" : undefined}>
          Todas
        </Link>
        {ESTADOS.map((e) => (
          <Link key={e} to={`/notificaciones?estado=${e}`} aria-current={estado === e ? "true" : undefined}>
            {etiqueta(e)}
          </Link>
        ))}
      </nav>
      {pagina ? (
        <p className="nota-tecnica" id="notificaciones-total">
          {pagina.total} en total.
        </p>
      ) : null}
      <TablaDatos
        leyenda="Notificaciones de alertas críticas"
        columnas={COLUMNAS}
        filas={pagina?.elementos ?? []}
        clave={(n) => n.notificacion_id}
        estado={consulta.isPending ? "cargando" : consulta.isError ? "error" : "listo"}
        error={consulta.isError ? mensajeDeError(consulta.error, "Las notificaciones", false) : undefined}
        alReintentar={() => void consulta.refetch()}
        vacia={<VistaVacia titulo="Todavía no se envió ninguna notificación" texto="Aparecen cuando entra una alerta crítica." />}
        {...(pagina ? { paginacion: { desplazamiento: pagina.desplazamiento, limite: pagina.limite, total: pagina.total } } : {})}
        alCambiarPagina={(nuevo) => {
          const siguiente = new URLSearchParams(parametros);
          siguiente.set("desplazamiento", String(nuevo));
          fijarParametros(siguiente);
        }}
      />
    </MarcoPagina>
  );
}
