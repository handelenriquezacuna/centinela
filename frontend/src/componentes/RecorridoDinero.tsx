import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { ErrorApi, pedir } from "../api/cliente";
import { useSesion } from "../api/sesion";
import type { CuentaSankey, ParametrosRed, ResultadoAprobacion, RespuestaSankey } from "../api/tiposRed";
import { DialogoConfirmacion } from "./DialogoConfirmacion";
import { Sankey, TablaSaltos } from "./Sankey";

export const MOTIVO_MIN = 5;
export const MOTIVO_MAX = 200;
/** Prefijo comun de las lecturas de la red: marcar una mula las vuelve a pedir todas. */
export const CLAVE_RED = ["red-mulas"] as const;

interface Propiedades {
  /** La cuenta de partida de la red (SINPE o CTA-). */
  cuenta: string;
  /** Si viene, el Sankey parte de esa transaccion y su cliente es la victima (columna 0). */
  transaccion?: string | undefined;
  parametros?: ParametrosRed;
}

interface Aviso {
  texto: string;
  error: boolean;
  cuenta: string | null;
}

interface Marca {
  cuenta: string;
  motivo: string;
}

function consultaDe(parametros: ParametrosRed | undefined, transaccion: string | undefined) {
  return {
    desde: parametros?.desde,
    ventana_minutos: parametros?.ventana_minutos,
    saltos: parametros?.saltos,
    transaccion,
  };
}

/**
 * El Sankey de una cuenta con lo que se hace sobre el: ver el recorrido, su tabla de saltos
 * y, solo si la sesion tiene `gestionar_listas`, marcar una cuenta como mula con un motivo
 * obligatorio (5 a 200) y confirmacion. Tras marcar, vuelve a pedir el Sankey (y la red):
 * el nodo cambia sin recargar la pagina. El servidor decide el permiso; esto solo evita
 * mostrar un boton cuya peticion iba a responder 403.
 */
export function RecorridoDinero({ cuenta, transaccion, parametros }: Propiedades) {
  const clienteConsultas = useQueryClient();
  const { data: sesion } = useSesion();
  const puedeGestionar = sesion?.permisos.includes("gestionar_listas") ?? false;
  const consulta = consultaDe(parametros, transaccion);
  const [motivos, setMotivos] = useState<Record<string, string>>({});
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<Marca | null>(null);

  const sankey = useQuery({
    queryKey: [...CLAVE_RED, "sankey", cuenta, consulta],
    queryFn: () => pedir<RespuestaSankey>(`/api/v1/red-mulas/${encodeURIComponent(cuenta)}/sankey`, { consulta }),
  });

  const marcar = useMutation({
    mutationFn: (marca: Marca) =>
      pedir<ResultadoAprobacion>("/api/v1/listas-riesgo", { metodo: "POST", cuerpo: { cuenta: marca.cuenta, motivo: marca.motivo } }),
    onSuccess: (resultado, marca) => {
      const id = resultado.entrada._id;
      setAviso({
        texto: resultado.creada
          ? `La cuenta ${marca.cuenta} quedó en lista de riesgo (${id}).`
          : `La cuenta ${marca.cuenta} ya estaba en lista de riesgo (${id}).`,
        error: false,
        cuenta: null,
      });
      setMotivos((previos) => ({ ...previos, [marca.cuenta]: "" }));
      void clienteConsultas.invalidateQueries({ queryKey: CLAVE_RED });
    },
    onError: (fallo, marca) => {
      setAviso({
        texto: `${fallo instanceof ErrorApi ? fallo.message : "No se pudo marcar la cuenta"}. No se marcó la cuenta ${marca.cuenta}.`,
        error: true,
        cuenta: marca.cuenta,
      });
    },
  });

  function pedirConfirmacion(evento: FormEvent, cuentaObjetivo: string) {
    evento.preventDefault();
    const motivo = (motivos[cuentaObjetivo] ?? "").trim();
    if (motivo.length < MOTIVO_MIN || motivo.length > MOTIVO_MAX) {
      setAviso({
        texto: `El motivo es obligatorio: entre ${MOTIVO_MIN} y ${MOTIVO_MAX} caracteres. No se marcó la cuenta ${cuentaObjetivo}.`,
        error: true,
        cuenta: cuentaObjetivo,
      });
      return;
    }
    setAviso(null);
    setPendiente({ cuenta: cuentaObjetivo, motivo });
  }

  function resolver(confirmada: boolean) {
    const marca = pendiente;
    setPendiente(null);
    if (confirmada && marca) marcar.mutate(marca);
  }

  if (sankey.isPending) {
    return <p className="nota-tecnica">Cargando el recorrido del dinero.</p>;
  }
  if (sankey.isError) {
    return (
      <div className="mensaje mensaje-error" role="alert" id="sankey-error">
        No se pudo cargar el recorrido del dinero: {sankey.error.message}
      </div>
    );
  }
  const disposicion = sankey.data.sankey;
  if (disposicion === null) {
    return (
      <p className="nota-tecnica" id="sankey-vacio">
        {sankey.data.motivo ?? "Esta cuenta no tiene salidas registradas en la ventana."}
      </p>
    );
  }

  function fila(c: CuentaSankey) {
    const idMotivo = `motivo-${c.cuenta}`;
    const conError = aviso?.error === true && aviso.cuenta === c.cuenta;
    return (
      <li key={`${c.cuenta}-${c.columna}`} data-cuenta={c.cuenta} data-en-lista={c.en_lista ? "si" : "no"} {...(c.victima ? { "data-victima": "si" } : {})}>
        <span className="id-corto">{c.cuenta}</span>
        {c.victima ? (
          <span className="insignia">Víctima: {c.nombre}</span>
        ) : c.en_lista ? (
          <span className="insignia es-critica">En lista de riesgo</span>
        ) : puedeGestionar ? (
          <form className="form-inline form-marca" noValidate onSubmit={(e) => pedirConfirmacion(e, c.cuenta)}>
            <label className="visualmente-oculto" htmlFor={idMotivo}>
              Motivo para marcar {c.cuenta}
            </label>
            <input
              id={idMotivo}
              name="motivo"
              type="text"
              required
              maxLength={MOTIVO_MAX}
              placeholder="Motivo (obligatorio)"
              aria-invalid={conError}
              aria-describedby={conError ? "sankey-mensaje" : undefined}
              value={motivos[c.cuenta] ?? ""}
              onChange={(e) => setMotivos((previos) => ({ ...previos, [c.cuenta]: e.target.value }))}
            />
            <button className="boton boton-primario" type="submit" disabled={marcar.isPending}>
              Marcar como mula
            </button>
          </form>
        ) : (
          <span className="nota-tecnica">Sin marca</span>
        )}
      </li>
    );
  }

  return (
    <div id="sankey" className="sankey-contenedor" data-saltos={disposicion.saltos} data-transferencias={disposicion.transferencias}>
      <link rel="stylesheet" href="/estaticos/sankey.css" />
      <div aria-live="polite">
        {aviso ? (
          <div className={`mensaje ${aviso.error ? "mensaje-error" : "mensaje-ok"}`} role={aviso.error ? "alert" : "status"} id="sankey-mensaje">
            {aviso.texto}
          </div>
        ) : null}
      </div>
      <Sankey disposicion={disposicion} />
      <p className="nota-tecnica">
        El grosor de cada banda es proporcional a los colones que pasaron de una cuenta a la siguiente. La columna azul es la
        víctima. Las cuentas con trama diagonal y la etiqueta &quot;En lista de riesgo&quot; ya tienen una entrada vigente.
      </p>
      <TablaSaltos disposicion={disposicion} />

      <h3 className="seccion-titulo">Cuentas del recorrido</h3>
      <ul className="sankey-cuentas" id="sankey-cuentas">
        {disposicion.cuentas.map(fila)}
      </ul>

      <DialogoConfirmacion abierto={pendiente !== null} titulo="Marcar como mula" etiquetaConfirmar="Marcar como mula" alResolver={resolver}>
        {pendiente ? (
          <p>
            Se marca la cuenta {pendiente.cuenta} como mula y las próximas transferencias hacia ella sumarán la regla R-07. Queda
            registro de quién lo hizo. Motivo: «{pendiente.motivo}». ¿Continuar?
          </p>
        ) : null}
      </DialogoConfirmacion>
    </div>
  );
}
