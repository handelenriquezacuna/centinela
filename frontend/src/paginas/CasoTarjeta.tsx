import { useRef, useState } from "react";
import { Link } from "react-router";

import { ErrorApi, pedir } from "../api/cliente";
import type { AccionLote, AlertaDeCaso, CasoDestino, EstadoAlerta, ResultadoLote } from "../api/tipos";
import { FechaCR } from "../componentes/FechaCR";
import { Insignia } from "../componentes/Insignia";
import { MontoColones } from "../componentes/MontoColones";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { etiqueta } from "../formatos";

const TERMINALES: ReadonlySet<EstadoAlerta> = new Set(["confirmada", "descartada", "escalada"]);

interface Propiedades {
  caso: CasoDestino;
  /** Se llama despues de aplicar un lote: la pantalla pide de nuevo el caso. */
  alTerminarLote: (cuenta: string) => void;
}

interface Resumen {
  aplicadas: number;
  solicitadas: number;
  destino: string;
  rechazadas: { alerta_id: string; estado_http: number; motivo: string }[];
  casosAbiertos: string[];
}

function motivo(item: ResultadoLote["resultados"][number], destino: EstadoAlerta): string {
  const actual = item.estado_actual ? etiqueta(item.estado_actual) : "";
  if (item.codigo === "no_encontrado") return "ya no existe";
  if (item.codigo === "transicion_invalida") return `no puede pasar de ${actual} a ${etiqueta(destino)}`;
  if (item.codigo === "conflicto") return `la tomó o resolvió otro agente (ahora está ${actual.toLowerCase()})`;
  return item.mensaje;
}

/**
 * Una cuenta destino: resumen, sugerencia, sus alertas con casilla y las acciones en lote.
 * Cada accion pide confirmacion en un <dialog> modal (foco y Esc nativos) y se aplica con
 * POST /api/v1/alertas/transiciones: cada alerta pasa sola por la maquina de estados.
 */
export function CasoTarjeta({ caso, alTerminarLote }: Propiedades) {
  const cuenta = caso.cuenta_destino;
  const [marcadas, setMarcadas] = useState<ReadonlySet<string>>(new Set());
  const [pendiente, setPendiente] = useState<AccionLote | null>(null);
  const [nota, setNota] = useState("");
  const [resumen, setResumen] = useState<Resumen | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialogo = useRef<HTMLDialogElement>(null);
  const confirmar = useRef<HTMLButtonElement>(null);
  const resultado = useRef<HTMLDivElement>(null);

  // Solo cuentan las marcadas que siguen en la cuenta: una alerta resuelta por otro
  // agente (llego por el canal) deja de estar seleccionada sola.
  const ids = new Set(caso.alertas.map((a) => a._id));
  const seleccion = [...marcadas].filter((id) => ids.has(id));
  const todas = caso.alertas.length > 0 && seleccion.length === caso.alertas.length;

  function alternar(id: string) {
    const nuevas = new Set(seleccion);
    if (nuevas.has(id)) nuevas.delete(id);
    else nuevas.add(id);
    setMarcadas(nuevas);
  }

  function abrir(accion: AccionLote) {
    if (seleccion.length === 0) {
      setError("Selecciona al menos una alerta de la cuenta.");
      return;
    }
    setError(null);
    setNota("");
    setPendiente(accion);
    dialogo.current?.showModal();
    confirmar.current?.focus();
  }

  async function aplicar(accion: AccionLote) {
    const motivoEscrito = nota.trim();
    try {
      const lote = await pedir<ResultadoLote>("/api/v1/alertas/transiciones", {
        metodo: "POST",
        cuerpo: {
          alertas: seleccion,
          destino: accion.destino,
          nota: `[lote en ${cuenta}] ${motivoEscrito}`.trim(),
        },
      });
      setResumen({
        aplicadas: lote.aplicadas,
        solicitadas: lote.solicitadas,
        destino: etiqueta(accion.destino),
        rechazadas: lote.resultados
          .filter((r) => !r.aplicada)
          .map((r) => ({ alerta_id: r.alerta_id, estado_http: r.estado_http, motivo: motivo(r, accion.destino) })),
        casosAbiertos: [...new Set(lote.resultados.map((r) => r.caso_id).filter((c): c is string => c !== null))],
      });
      setMarcadas(new Set());
    } catch (fallo) {
      setError(fallo instanceof ErrorApi ? fallo.message : "No se pudo aplicar la acción");
    } finally {
      alTerminarLote(cuenta);
      resultado.current?.focus();
    }
  }

  function cerrarDialogo() {
    const accion = pendiente;
    const elegido = dialogo.current?.returnValue;
    if (dialogo.current) dialogo.current.returnValue = "";
    setPendiente(null);
    if (elegido === "confirmar" && accion) {
      void aplicar(accion);
    }
  }

  const columnas: Columna<AlertaDeCaso>[] = [
    {
      clave: "marca",
      titulo: "Seleccionar",
      encabezado: (
        <input
          type="checkbox"
          checked={todas}
          onChange={() => setMarcadas(todas ? new Set() : new Set(ids))}
          aria-label={`Seleccionar todas las alertas de la cuenta destino ${cuenta}`}
        />
      ),
      celda: (alerta) => (
        <input type="checkbox" checked={seleccion.includes(alerta._id)} onChange={() => alternar(alerta._id)} aria-label={`Seleccionar ${alerta._id}`} />
      ),
    },
    { clave: "id", titulo: "Alerta", celda: (a) => <Link to={`/detalle/${a._id}`}>{a._id}</Link>, clase: "alerta-id" },
    { clave: "severidad", titulo: "Severidad", celda: (a) => <Insignia tipo="severidad" valor={a.severidad} /> },
    { clave: "puntaje", titulo: "Puntaje", celda: (a) => a.puntaje },
    { clave: "cliente", titulo: "Cliente", celda: (a) => a.cliente_nombre },
    { clave: "origen", titulo: "Origen", celda: (a) => a.cuenta_origen, clase: "alerta-cuentas" },
    { clave: "monto", titulo: "Monto", celda: (a) => <MontoColones valor={a.monto_crc} /> },
    { clave: "creada", titulo: "Creada (hora CR)", celda: (a) => <FechaCR valor={a.fecha_creacion} /> },
    { clave: "estado", titulo: "Estado", celda: (a) => <Insignia tipo="estado" valor={a.estado} /> },
  ];

  const tituloId = `caso-${cuenta}-titulo`;
  return (
    <section
      id={`caso-${cuenta}`}
      className={`caso severidad-${caso.severidad_max}`}
      data-destino={cuenta}
      aria-labelledby={tituloId}
    >
      <div ref={resultado} tabIndex={-1}>
        {error ? (
          <div className="mensaje mensaje-error" role="alert">
            {error}
          </div>
        ) : null}
        {resumen ? (
          <div
            className={`mensaje ${resumen.rechazadas.length ? "mensaje-error" : "mensaje-ok"}`}
            role="status"
            data-resultado-lote
          >
            <p>
              <strong>
                Pasaron {resumen.aplicadas} de {resumen.solicitadas}
              </strong>{" "}
              a {resumen.destino}.
            </p>
            {resumen.rechazadas.length ? (
              <>
                <p>No pasaron:</p>
                <ul>
                  {resumen.rechazadas.map((r) => (
                    <li key={r.alerta_id}>
                      <span className="id-corto">{r.alerta_id}</span>: {r.motivo}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {resumen.casosAbiertos.map((c) => (
              <p key={c}>
                Caso de investigación abierto: <span className="id-corto">{c}</span>
              </p>
            ))}
          </div>
        ) : null}
      </div>

      <header className="caso-cabecera">
        <h2 id={tituloId} className="caso-titulo">
          Cuenta <span className="id-corto">{cuenta}</span>{" "}
          {caso.en_lista_riesgo ? (
            <span className="insignia es-critica" title="Vigente en listas de riesgo">
              En lista de riesgo
            </span>
          ) : null}
        </h2>
        <dl className="caso-resumen">
          <div>
            <dt>Alertas</dt>
            <dd className="caso-alertas">{caso.total_alertas}</dd>
          </div>
          <div>
            <dt>Víctimas</dt>
            <dd className="caso-victimas">{caso.victimas}</dd>
          </div>
          <div>
            <dt>Monto total</dt>
            <dd className="caso-monto">
              <MontoColones valor={caso.monto_total_crc} />
            </dd>
          </div>
          <div>
            <dt>Severidad máxima</dt>
            <dd className="caso-severidad">
              <Insignia tipo="severidad" valor={caso.severidad_max} />
            </dd>
          </div>
          <div>
            <dt>Puntaje máximo</dt>
            <dd>{caso.puntaje_max}</dd>
          </div>
          <div>
            <dt>Más antigua (hora CR)</dt>
            <dd>
              <FechaCR valor={caso.mas_antigua} />
            </dd>
          </div>
          <div>
            <dt>Reglas que más se repiten</dt>
            <dd>{caso.reglas.length ? caso.reglas.map((r) => `${r.codigo} (${r.veces})`).join(", ") : "-"}</dd>
          </div>
        </dl>
        {caso.sugerencia ? (
          <p className="caso-sugerencia" data-sugerencia>
            Sugerencia: {caso.sugerencia} <small>Decide el agente; nada se cierra solo.</small>
          </p>
        ) : null}
      </header>

      <TablaDatos
        leyenda={`Alertas abiertas hacia la cuenta destino ${cuenta}`}
        columnas={columnas}
        filas={caso.alertas}
        clave={(alerta) => alerta._id}
        estado="listo"
        atributosFila={(alerta) => ({
          id: `alerta-${alerta._id}`,
          className: `severidad-${alerta.severidad}`,
          "data-fila-atajo": "",
          "data-estado": alerta.estado,
          "data-puntaje": alerta.puntaje,
          "data-monto-crc": alerta.monto_crc,
          "data-destino": alerta.cuenta_destino,
        })}
      />
      {caso.alertas_ocultas ? (
        <p className="nota-tecnica">
          Se muestran las {caso.alertas.length} alertas de mayor puntaje; hay {caso.alertas_ocultas} más hacia esta
          cuenta.
        </p>
      ) : null}

      <div className="grupo-botones acciones-lote">
        <span className="caso-seleccion" aria-live="polite">
          {seleccion.length} seleccionadas
        </span>
        {caso.acciones.map((accion) => (
          <button key={accion.destino} type="button" className="boton" onClick={() => abrir(accion)}>
            {accion.rotulo}
          </button>
        ))}
      </div>

      <dialog
        ref={dialogo}
        className="dialogo"
        aria-labelledby={`${tituloId}-dialogo`}
        onClose={cerrarDialogo}
      >
        <form onSubmit={(e) => e.preventDefault()}>
          <h2 id={`${tituloId}-dialogo`}>{pendiente ? `${pendiente.rotulo}: confirmar` : "Confirmar acción"}</h2>
          {pendiente ? (
            <p>
              Vas a aplicar «{pendiente.rotulo}» a {seleccion.length} {seleccion.length === 1 ? "alerta" : "alertas"}{" "}
              hacia la cuenta destino {cuenta}. Cada una pasa por la máquina de estados; las que no puedan pasar se
              informan al terminar.
              {TERMINALES.has(pendiente.destino) ? " Esta acción cierra las alertas y no se puede deshacer." : ""}
            </p>
          ) : null}
          <div className="campo">
            <label htmlFor={`${tituloId}-motivo`}>Motivo (se agrega al historial de cada alerta)</label>
            <textarea
              id={`${tituloId}-motivo`}
              rows={3}
              maxLength={900}
              placeholder="Opcional"
              value={nota}
              onChange={(e) => setNota(e.target.value)}
            />
          </div>
          <div className="grupo-botones">
            <button type="button" className="boton boton-secundario" onClick={() => dialogo.current?.close("cancelar")}>
              Cancelar
            </button>
            <button
              type="button"
              className="boton boton-primario"
              ref={confirmar}
              onClick={() => dialogo.current?.close("confirmar")}
            >
              Confirmar
            </button>
          </div>
        </form>
      </dialog>
    </section>
  );
}
