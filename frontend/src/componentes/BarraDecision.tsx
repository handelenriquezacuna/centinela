import { useState } from "react";
import { Link } from "react-router";

import type { EstadoAlerta, RespuestaCliente, Severidad } from "../api/tipos";
import type { AccionDisponible, SugerenciaCliente } from "../api/tiposDetalle";
import { etiqueta } from "../formatos";
import { AvisoRegion, type ResultadoAviso } from "./AvisoRegion";
import { DialogoConfirmacion } from "./DialogoConfirmacion";
import { Insignia } from "./Insignia";
import { InsigniaCliente } from "./InsigniaCliente";

const CLASE_BOTON: Record<string, string> = {
  en_revision: "boton-primario",
  confirmada: "boton-confirmar",
  descartada: "boton-descartar",
  escalada: "boton-escalar",
};

interface Propiedades {
  alertaId: string;
  estado: EstadoAlerta;
  severidad: Severidad;
  /** Las acciones que permite la maquina de estados desde `estado` (las manda el servidor). */
  acciones: AccionDisponible[];
  /** Hay una accion en vuelo: se deshabilitan los botones. */
  ocupado?: boolean;
  /** Se llama al elegir una accion que no cierra, o al confirmar una que cierra. */
  alResolver: (accion: AccionDisponible, nota: string) => void;
  resultado?: ResultadoAviso;
  /** Quien actua, para que su nombre en el historial no sea una sorpresa. */
  actuaComo?: string | null;
  /** La alerta siguiente de la cola: `undefined` mientras se busca, `null` si no hay mas. */
  siguiente?: string | null | undefined;
  /** Caso que se abrio al escalar. */
  casoId?: string | null;
  /** T-82: lo que el cliente contesto a su SMS, y lo que eso sugiere. Sugiere; decide el agente. */
  respuestaCliente?: RespuestaCliente | null | undefined;
  sugerenciaCliente?: SugerenciaCliente | null | undefined;
}

/**
 * La barra de decision del detalle: estado, severidad, las acciones de la maquina de
 * estados y la siguiente alerta de la cola. Queda fija arriba, de modo que decidir no
 * obliga a desplazarse. Las acciones que cierran piden confirmacion en un dialogo.
 */
export function BarraDecision({ alertaId, estado, severidad, acciones, ocupado = false, alResolver, resultado = null, actuaComo, siguiente, casoId, respuestaCliente, sugerenciaCliente }: Propiedades) {
  // La nota se escribio para un estado: al cambiar (la accion se aplico) deja de valer.
  const [escrita, setEscrita] = useState({ estado, texto: "" });
  const nota = escrita.estado === estado ? escrita.texto : "";
  const setNota = (texto: string) => setEscrita({ estado, texto });
  const [pendiente, setPendiente] = useState<AccionDisponible | null>(null);

  function elegir(accion: AccionDisponible) {
    if (accion.cierra) setPendiente(accion);
    else alResolver(accion, nota.trim());
  }

  function resolver(confirmada: boolean) {
    const accion = pendiente;
    setPendiente(null);
    if (confirmada && accion) alResolver(accion, nota.trim());
  }

  return (
    <section className="barra-decision" id="acciones" aria-label="Decisión sobre la alerta">
      <div className="barra-estado">
        <Insignia tipo="estado" valor={estado} id="insignia-estado" />
        <Insignia tipo="severidad" valor={severidad} texto={`Severidad ${etiqueta(severidad).toLowerCase()}`} />
        <InsigniaCliente respuesta={respuestaCliente} id="insignia-cliente" />
        {actuaComo ? (
          <span className="nota-tecnica" id="actuas-como">
            Actúas como <strong>{actuaComo}</strong>
          </span>
        ) : null}
      </div>

      {sugerenciaCliente ? (
        <p
          className={`mensaje ${sugerenciaCliente.valor === "no_fui_yo" ? "mensaje-error" : "mensaje-ok"}`}
          id="sugerencia-cliente"
          data-destino-sugerido={sugerenciaCliente.destino_sugerido}
        >
          {sugerenciaCliente.texto}
        </p>
      ) : null}

      {acciones.length > 0 ? (
        <div className="barra-acciones" id="form-resolver">
          <div className="campo barra-nota">
            <label htmlFor="nota-resolucion">Nota (se agrega al historial)</label>
            <input
              type="text"
              id="nota-resolucion"
              name="nota"
              maxLength={1000}
              placeholder="Motivo de la decisión (opcional)"
              value={nota}
              onChange={(e) => setNota(e.target.value)}
            />
          </div>
          <div className="grupo-botones">
            {acciones.map((accion) => (
              <button
                key={accion.destino}
                type="button"
                className={`boton ${CLASE_BOTON[accion.destino] ?? ""}`}
                data-destino={accion.destino}
                aria-describedby={`ayuda-${accion.destino}`}
                title={accion.ayuda}
                disabled={ocupado}
                onClick={() => elegir(accion)}
              >
                {accion.rotulo}
              </button>
            ))}
          </div>
          {acciones.map((accion) => (
            <span key={accion.destino} id={`ayuda-${accion.destino}`} className="visualmente-oculto">
              {accion.ayuda}
            </span>
          ))}
        </div>
      ) : (
        <p className="nota-tecnica barra-cerrada">
          La alerta está {etiqueta(estado).toLowerCase()}: no admite más acciones.
        </p>
      )}

      <div className="barra-siguiente">
        {siguiente ? (
          <Link className={acciones.length === 0 ? "boton boton-primario" : "boton boton-secundario"} to={`/detalle/${siguiente}`} id="siguiente-alerta">
            Siguiente alerta
          </Link>
        ) : (
          <span className="nota-tecnica" id="sin-siguiente">
            {siguiente === undefined ? "Buscando la siguiente alerta…" : "No hay más alertas abiertas en la cola."}
          </span>
        )}
      </div>

      <div aria-live="polite" className="barra-aviso">
        <AvisoRegion resultado={resultado} />
        {casoId ? (
          <p className="mensaje mensaje-ok" id="caso-creado">
            Caso abierto: <span className="id-corto">{casoId}</span> (en investigación)
          </p>
        ) : null}
      </div>

      <DialogoConfirmacion
        abierto={pendiente !== null}
        titulo={pendiente ? `${pendiente.rotulo}: confirmar` : "Confirmar acción"}
        etiquetaConfirmar={pendiente?.rotulo ?? "Confirmar"}
        alResolver={resolver}
      >
        {pendiente ? (
          <p>
            ¿{pendiente.rotulo}? Esta acción cierra la alerta {alertaId} y no se puede deshacer.
            {nota.trim() ? ` Nota: «${nota.trim()}».` : ""}
          </p>
        ) : null}
      </DialogoConfirmacion>
    </section>
  );
}
