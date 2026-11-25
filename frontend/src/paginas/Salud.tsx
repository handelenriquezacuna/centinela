import { useQuery } from "@tanstack/react-query";

import { pedir } from "../api/cliente";
import type { EstadoFuncional, SaludBase, TareaSupervisada } from "../api/tipos";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { TarjetaMetrica } from "../componentes/TarjetaMetrica";
import { VistaError } from "../componentes/VistaError";
import { decimal, etiqueta } from "../formatos";
import { mensajeDeError } from "../reglas/errores";
import { tiempoCorto } from "../tablero/formatos";

const CADA_MS = 10_000;

const ESTADO_GENERAL: Record<EstadoFuncional["estado"], string> = {
  ok: "Todo funciona",
  degradado: "Degradado",
  sin_supervision: "Sin supervisión",
};

const NOMBRES_TAREA: Record<string, string> = {
  latido: "Latido de la base",
  d1_deteccion: "D1 · Detección",
  d2_listas: "D2 · Listas de riesgo",
  d3_indicadores: "D3 · Indicadores",
};

function nombreTarea(nombre: string): string {
  return NOMBRES_TAREA[nombre] ?? etiqueta(nombre);
}

function milisegundos(valor: number | null | undefined): string {
  return valor === null || valor === undefined ? "—" : `${Math.round(valor).toLocaleString("es-CR")} ms`;
}

const COLUMNAS: Columna<TareaSupervisada>[] = [
  {
    clave: "tarea",
    titulo: "Tarea",
    celda: (t) => (
      <>
        <strong>{nombreTarea(t.nombre)}</strong>
        {t.advertencia ? <div className="nota-tecnica">{t.advertencia}</div> : null}
        {t.ultimo_error ? <div className="nota-tecnica">Último error: {t.ultimo_error.mensaje}</div> : null}
      </>
    ),
  },
  { clave: "estado", titulo: "Estado", celda: (t) => <span className={`insignia insignia-estado es-tarea-${t.estado}`}>{etiqueta(t.estado)}</span> },
  { clave: "eventos", titulo: "Eventos", celda: (t) => t.eventos_procesados.toLocaleString("es-CR"), clase: "monto" },
  { clave: "ultimo", titulo: "Último evento", celda: (t) => <FechaCR valor={t.ultimo_evento} /> },
  {
    clave: "retraso",
    titulo: "Retraso",
    celda: (t) => (t.retraso_segundos === null ? "—" : tiempoCorto(Math.round(t.retraso_segundos))),
  },
  {
    clave: "latencia",
    titulo: "Latencia p50 / p95",
    celda: (t) => (t.latencia_ms && t.latencia_ms.n > 0 ? `${milisegundos(t.latencia_ms.p50)} / ${milisegundos(t.latencia_ms.p95)}` : "—"),
  },
  {
    clave: "rezago",
    titulo: "Por evaluar",
    celda: (t) => (t.rezago ? `${t.rezago.pendientes.toLocaleString("es-CR")}` : "—"),
    clase: "monto",
  },
];

/** Salud del sistema (solo el administrador): `/estado` y `/salud` con las piezas del portal, no el JSON crudo. */
export function Salud() {
  const estado = useQuery({
    queryKey: ["estado"],
    queryFn: () => pedir<EstadoFuncional>("/estado"),
    refetchInterval: CADA_MS,
  });
  const salud = useQuery({
    queryKey: ["salud"],
    queryFn: () => pedir<SaludBase>("/salud"),
    refetchInterval: CADA_MS,
  });
  const base = salud.data;
  const general = estado.data;
  const d1 = general?.tareas.find((t) => t.nombre === "d1_deteccion");

  return (
    <MarcoPagina id="salud" ancho="ancho">
      <EncabezadoPagina
        id="salud"
        titulo="Salud del sistema"
        descripcion="Si la base responde y si el detector está haciendo su trabajo. Se renueva cada 10 segundos."
        acciones={
          <button
            type="button"
            className="boton boton-secundario"
            onClick={() => {
              void estado.refetch();
              void salud.refetch();
            }}
          >
            Actualizar
          </button>
        }
      />
      {estado.isError ? <VistaError error={mensajeDeError(estado.error, "El estado")} alReintentar={() => void estado.refetch()} /> : null}
      {salud.isError ? <VistaError error={mensajeDeError(salud.error, "La salud de la base")} alReintentar={() => void salud.refetch()} /> : null}

      <section className="tarjeta" aria-labelledby="titulo-resumen-salud">
        <h2 id="titulo-resumen-salud" className="seccion-titulo">
          Resumen
        </h2>
        <div className="metricas">
          <TarjetaMetrica
            id="salud-general"
            rotulo="Estado del sistema"
            valor={general ? ESTADO_GENERAL[general.estado] : ""}
            estado={general ? (general.estado === "ok" ? "normal" : "alerta") : "sin_datos"}
            nota={general ? `Versión ${general.version}, entorno ${general.entorno}` : undefined}
          />
          <TarjetaMetrica
            id="salud-base"
            rotulo="Base de datos"
            valor={base?.mongo_conectado ? "Conectada" : "Sin conexión"}
            estado={base ? (base.mongo_conectado ? "normal" : "alerta") : "sin_datos"}
            nota={base?.detalle ?? undefined}
          />
          <TarjetaMetrica
            id="salud-replica"
            rotulo="Conjunto de réplicas"
            valor={base?.replica_set ?? "—"}
            estado={base?.replica_set ? "normal" : "sin_datos"}
            nota={base?.es_primario === null || base?.es_primario === undefined ? undefined : base.es_primario ? "Nodo primario" : "Nodo secundario"}
          />
          <TarjetaMetrica
            id="salud-d1"
            rotulo="Detección (D1): por evaluar"
            valor={d1?.rezago ? d1.rezago.pendientes.toLocaleString("es-CR") : "—"}
            estado={d1?.rezago ? (d1.estado === "corriendo" ? "normal" : "alerta") : "sin_datos"}
            nota={
              d1?.latencia_ms && d1.latencia_ms.n > 0
                ? `Latencia p95 ${milisegundos(d1.latencia_ms.p95)} en ${d1.latencia_ms.n} mediciones${
                    d1.rezago?.atraso_segundos != null ? `; atraso ${decimal(d1.rezago.atraso_segundos, 1)} s` : ""
                  }`
                : undefined
            }
          />
        </div>
        {general && general.advertencias.length > 0 ? (
          <div className="mensaje mensaje-error" role="status">
            <ul>
              {general.advertencias.map((aviso) => (
                <li key={aviso}>{aviso}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <section className="tarjeta" aria-labelledby="titulo-tareas-salud">
        <h2 id="titulo-tareas-salud" className="seccion-titulo">
          Tareas de fondo
        </h2>
        <TablaDatos
          leyenda="Tareas de fondo del proceso y su último trabajo"
          columnas={COLUMNAS}
          filas={general?.tareas ?? []}
          clave={(t) => t.nombre}
          estado={estado.isPending ? "cargando" : estado.isError ? "error" : "listo"}
          error={estado.isError ? mensajeDeError(estado.error, "El estado") : undefined}
        />
      </section>
    </MarcoPagina>
  );
}
