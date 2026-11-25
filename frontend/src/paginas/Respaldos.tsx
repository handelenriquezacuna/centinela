import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { pedir } from "../api/cliente";
import type { ListaRespaldos, Respaldo } from "../api/tipos";
import { AvisoRegion, type ResultadoAviso } from "../componentes/AvisoRegion";
import { DialogoConfirmacion } from "../componentes/DialogoConfirmacion";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { mensajeDeError } from "../reglas/errores";

const CLAVE_RESPALDOS = ["respaldos"] as const;

function tamano(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function total(conteos: Record<string, number>): number {
  return Object.values(conteos).reduce((suma, n) => suma + n, 0);
}

/** El comando que restaura. NO es un boton: reemplaza los datos vivos y se corre en consola. */
function comandoRestaurar(respaldo: Respaldo): string {
  return `python tareas.py restaurar --archivo ${respaldo.archivo} --si`;
}

function Verificacion({ respaldo }: { respaldo: Respaldo }) {
  const v = respaldo.verificacion;
  if (v === null) return <span>Sin verificar</span>;
  return (
    <div>
      <span className={`insignia ${v.resultado === "correcta" ? "es-media" : "es-critica"}`}>
        {v.resultado === "correcta" ? "Correcta" : "Fallida"}
      </span>{" "}
      <FechaCR valor={v.verificado_utc} /> por {v.verificado_por}
      {v.detalle ? <p className="nota-tecnica">{v.detalle}</p> : null}
      {v.diferencias.length > 0 ? (
        <ul>
          {v.diferencias.map((d) => (
            <li key={d.coleccion}>
              <code>{d.coleccion}</code>: se esperaban {d.esperados_desde}
              {d.esperados_hasta !== d.esperados_desde ? ` a ${d.esperados_hasta}` : ""} y se restauraron {d.restaurados}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * Respaldos de la base (D-74), area de Administracion. Crear corre `mongodump`; Verificar
 * restaura el archivo en una base temporal, compara conteos y la borra. Restaurar sobre la
 * base viva NO esta aqui a proposito: la pantalla muestra el comando.
 */
export function Respaldos() {
  const cliente = useQueryClient();
  const [confirmando, setConfirmando] = useState(false);
  const [aviso, setAviso] = useState<ResultadoAviso>(null);
  const lista = useQuery({ queryKey: CLAVE_RESPALDOS, queryFn: () => pedir<ListaRespaldos>("/api/v1/respaldos") });
  const refrescar = () => cliente.invalidateQueries({ queryKey: CLAVE_RESPALDOS });
  const crear = useMutation({
    mutationFn: () => pedir<Respaldo>("/api/v1/respaldos", { metodo: "POST" }),
    onSuccess: (r) => setAviso({ tipo: "ok", texto: `Respaldo creado: ${r.respaldo_id}` }),
    onError: (e) => setAviso({ tipo: "error", error: mensajeDeError(e, "El respaldo", false) }),
    onSettled: refrescar,
  });
  const verificar = useMutation({
    mutationFn: (id: string) => pedir<Respaldo>(`/api/v1/respaldos/${id}/verificar`, { metodo: "POST" }),
    onSuccess: (r) =>
      setAviso(
        r.verificacion?.resultado === "correcta"
          ? { tipo: "ok", texto: `Verificación correcta: ${r.respaldo_id}` }
          : { tipo: "error", error: { texto: `Verificación fallida: ${r.respaldo_id}`, detalles: r.verificacion?.detalle ? [r.verificacion.detalle] : [] } },
      ),
    onError: (e) => setAviso({ tipo: "error", error: mensajeDeError(e, "La verificación", false) }),
    onSettled: refrescar,
  });
  const ocupado = crear.isPending || verificar.isPending || (lista.data?.en_curso ?? false);

  const columnas: Columna<Respaldo>[] = [
    {
      clave: "respaldo",
      titulo: "Respaldo",
      celda: (r) => (
        <>
          <code>{r.respaldo_id}</code>
          <div className="nota-tecnica">{r.base}</div>
        </>
      ),
    },
    {
      clave: "creado",
      titulo: "Creado",
      celda: (r) => (
        <>
          <FechaCR valor={r.creado_utc} />
          <div className="nota-tecnica">{r.creado_por}</div>
        </>
      ),
    },
    { clave: "tamano", titulo: "Tamaño", celda: (r) => tamano(r.tamano_bytes) },
    {
      clave: "documentos",
      titulo: "Documentos",
      celda: (r) => `${total(r.documentos_por_coleccion)} en ${Object.keys(r.documentos_por_coleccion).length} colecciones`,
    },
    {
      clave: "verificacion",
      titulo: "Verificación",
      celda: (r) => (
        <>
          <Verificacion respaldo={r} />
          <button
            type="button"
            className="boton boton-secundario"
            disabled={ocupado}
            aria-label={`Verificar ${r.respaldo_id}`}
            onClick={() => {
              setAviso(null);
              verificar.mutate(r.respaldo_id);
            }}
          >
            {verificar.isPending && verificar.variables === r.respaldo_id ? "Verificando…" : "Verificar"}
          </button>
        </>
      ),
    },
    { clave: "restaurar", titulo: "Restaurar", celda: (r) => <code>{comandoRestaurar(r)}</code> },
  ];

  return (
    <MarcoPagina id="respaldos" ancho="ancho">
      <EncabezadoPagina
        id="respaldos"
        titulo="Respaldos"
        descripcion="Cada respaldo es un archivo de mongodump con su manifiesto. Verificar lo restaura en una base temporal, compara los conteos con el manifiesto y la borra. Una operación a la vez."
        acciones={
          <button type="button" className="boton boton-primario" disabled={ocupado} onClick={() => setConfirmando(true)}>
            {crear.isPending ? "Creando…" : "Crear respaldo"}
          </button>
        }
      />
      <AvisoRegion resultado={aviso} />
      <section className="tarjeta" aria-labelledby="titulo-respaldos">
        <h2 id="titulo-respaldos" className="seccion-titulo">
          Respaldos ({lista.data?.total ?? 0})
        </h2>
        <TablaDatos
          leyenda="Respaldos de la base"
          columnas={columnas}
          filas={lista.data?.elementos ?? []}
          clave={(r) => r.respaldo_id}
          estado={lista.isPending ? "cargando" : lista.isError ? "error" : "listo"}
          error={lista.isError ? mensajeDeError(lista.error, "La lista de respaldos", false) : undefined}
          alReintentar={() => void lista.refetch()}
          vacia={<p className="subtitulo">Todavía no hay respaldos.</p>}
        />
      </section>
      <p className="nota-tecnica">
        Restaurar reemplaza los datos vivos, por eso no es un botón: se corre en la consola del servidor con el comando de la
        tabla, que antes hace un respaldo previo automático.
      </p>
      <DialogoConfirmacion
        abierto={confirmando}
        titulo="Crear un respaldo"
        etiquetaConfirmar="Crear respaldo"
        alResolver={(si) => {
          setConfirmando(false);
          if (si) {
            setAviso(null);
            crear.mutate();
          }
        }}
      >
        Se copiará la base con mongodump. Puede tardar unos segundos y no detiene el sistema.
      </DialogoConfirmacion>
    </MarcoPagina>
  );
}
