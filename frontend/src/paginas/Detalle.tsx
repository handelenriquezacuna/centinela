import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ErrorApi, pedir } from "../api/cliente";
import { useSesion } from "../api/sesion";
import type { AccionDisponible, DetalleAlerta, ResultadoTransicion } from "../api/tiposDetalle";
import { DialogoConfirmacion } from "../componentes/DialogoConfirmacion";
import { RecorridoDinero } from "../componentes/RecorridoDinero";
import { colones, etiqueta, horaCostaRica } from "../formatos";

const CLASE_BOTON: Record<string, string> = {
  en_revision: "boton-primario",
  confirmada: "boton-confirmar",
  descartada: "boton-descartar",
  escalada: "boton-escalar",
};

interface Aviso {
  texto: string;
  error: boolean;
}

function claveDetalle(alertaId: string) {
  return ["detalle", alertaId] as const;
}

/**
 * Detalle de una alerta (H-18): la explicacion por regla con su evidencia, los cortes de
 * severidad, el recorrido del dinero, las acciones que permite la maquina de estados y el
 * historial. Datos: GET /api/v1/alertas/{id}/detalle. Resolver: POST .../transiciones.
 */
export function Detalle() {
  const { alertaId = "" } = useParams();
  const consulta = useQuery({
    queryKey: claveDetalle(alertaId),
    queryFn: () => pedir<DetalleAlerta>(`/api/v1/alertas/${encodeURIComponent(alertaId)}/detalle`),
  });

  if (consulta.isPending) {
    return <p role="status">Cargando la alerta…</p>;
  }
  if (consulta.isError) {
    const noExiste = consulta.error instanceof ErrorApi && consulta.error.estado === 404;
    return (
      <>
        <p className="migaja">
          <Link to="/panel">Panel</Link> / Alerta {alertaId}
        </p>
        <div className="mensaje mensaje-error" role="alert" id="detalle-error">
          {noExiste ? `No existe la alerta ${alertaId}.` : `No se pudo cargar la alerta: ${consulta.error.message}`}
        </div>
      </>
    );
  }
  return <DetalleCargado detalle={consulta.data} />;
}

function DetalleCargado({ detalle }: { detalle: DetalleAlerta }) {
  const { alerta, transaccion, cliente, perfil, puntaje, politica } = detalle;
  const nombres = detalle.nombres_agentes;
  const nombreAgente = alerta.agente_id ? nombres[alerta.agente_id] : undefined;
  const instante = transaccion?.fecha ?? alerta.fecha_creacion;
  const consultaRed = `?desde=${encodeURIComponent(instante)}`;
  return (
    <>
      <link rel="stylesheet" href="/estaticos/detalle.css" />
      <p className="migaja">
        <Link to="/panel">Panel</Link> / Alerta {alerta._id}
      </p>

      <div className="titulo-pantalla">
        <div>
          <h1>Alerta {alerta._id}</h1>
          <p className="subtitulo">
            Transacción <span className="id-corto">{alerta.transaccion_id}</span> · creada {horaCostaRica(alerta.fecha_creacion)} (hora CR)
          </p>
        </div>
      </div>

      <section className="tarjeta" aria-label="Resumen de la alerta">
        <div className="resumen-alerta">
          <div>
            <span className={`insignia es-${alerta.severidad.toLowerCase()}`}>Severidad {etiqueta(alerta.severidad).toLowerCase()}</span>
          </div>
          <div className="puntaje">
            {alerta.puntaje}
            <small>puntaje / {puntaje.maximo}</small>
          </div>
          <div>
            <span id="insignia-estado" className={`insignia insignia-estado es-${alerta.estado}`}>
              {etiqueta(alerta.estado)}
            </span>
          </div>
          <div>
            <dl className="lista-datos">
              <div>
                <dt>Agente asignado</dt>
                <dd id="agente-asignado">
                  {alerta.agente_id ?? etiqueta("sin_asignar")}
                  {nombreAgente ? ` (${nombreAgente})` : ""}
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </section>

      <section className="tarjeta" aria-label="Datos de la transacción">
        <h2 className="seccion-titulo">Transacción</h2>
        <dl className="lista-datos">
          <div>
            <dt>Id de transacción</dt>
            <dd className="id-corto">{alerta.transaccion_id}</dd>
          </div>
          <div>
            <dt>Cuenta origen</dt>
            <dd className="id-corto">
              {alerta.cuenta_origen}
              {transaccion ? ` (${transaccion.cuenta_origen})` : ""}
            </dd>
          </div>
          <div>
            <dt>Cuenta destino</dt>
            <dd className="id-corto">
              {alerta.cuenta_destino}{" "}
              <Link id="enlace-red-mulas" to={`/red-mulas/${alerta.cuenta_destino}${consultaRed}`}>
                ver red de mulas
              </Link>
            </dd>
          </div>
          <div>
            <dt>Monto</dt>
            <dd className="monto">{colones(alerta.monto_crc)}</dd>
          </div>
          {transaccion ? (
            <div>
              <dt>Canal</dt>
              <dd>{etiqueta(transaccion.canal)}</dd>
            </div>
          ) : null}
          <div>
            <dt>Fecha y hora (hora CR)</dt>
            <dd className="hora">{horaCostaRica(instante)}</dd>
          </div>
        </dl>
        {!transaccion ? <p className="nota-tecnica">No se encontró la transacción; se muestran los datos copiados en la alerta.</p> : null}
      </section>

      <section className="tarjeta" aria-label="Recorrido del dinero">
        <h2 className="seccion-titulo">Recorrido del dinero</h2>
        <RecorridoDinero cuenta={alerta.cuenta_destino} transaccion={alerta.transaccion_id} parametros={{ desde: instante }} />
      </section>

      <section className="tarjeta" aria-label="Perfil del cliente">
        <h2 className="seccion-titulo">Perfil del cliente</h2>
        <dl className="lista-datos">
          <div>
            <dt>Cliente</dt>
            <dd>
              {cliente ? cliente.nombre : alerta.cliente_nombre}
              {cliente ? ` (${cliente.cliente_id})` : ""}
            </dd>
          </div>
          {cliente?.cedula ? (
            <div>
              <dt>Cédula</dt>
              <dd>{cliente.cedula}</dd>
            </div>
          ) : null}
          {perfil ? (
            <>
              <div>
                <dt>Transferencias</dt>
                <dd>{perfil.num_transferencias}</dd>
              </div>
              <div>
                <dt>Monto promedio</dt>
                <dd className="monto">{colones(perfil.promedio_crc)}</dd>
              </div>
              <div>
                <dt>Monto máximo</dt>
                <dd className="monto">{colones(perfil.maximo_crc)}</dd>
              </div>
              <div>
                <dt>Horario habitual</dt>
                <dd>
                  {perfil.horario_habitual?.inicio} - {perfil.horario_habitual?.fin} (hora CR)
                </dd>
              </div>
              <div>
                <dt>Destinos frecuentes</dt>
                <dd>{perfil.destinos_frecuentes.length}</dd>
              </div>
            </>
          ) : null}
        </dl>
        {!perfil ? <p className="nota-tecnica">El cliente no tiene perfil materializado.</p> : null}
      </section>

      <section className="tarjeta" aria-label="Reglas que dispararon">
        <h2 className="seccion-titulo">Reglas que dispararon (aporte al puntaje)</h2>
        {detalle.es_v2 ? (
          <>
            <table className="tabla-aportes" id="tabla-aportes">
              <thead>
                <tr>
                  <th scope="col">Regla</th>
                  <th scope="col">Versión</th>
                  <th scope="col">Explicación</th>
                  <th scope="col">Aporte</th>
                  <th scope="col">Acumulado</th>
                </tr>
              </thead>
              <tbody>
                {puntaje.filas.map((regla) => (
                  <tr key={regla.regla_id}>
                    <td>
                      <strong>{regla.codigo}</strong> <small>{regla.regla_id}</small>
                    </td>
                    <td>v{regla.version}</td>
                    <td>
                      {regla.explicacion}
                      {regla.evidencia ? <span className="evidencia">Evidencia: {regla.evidencia}</span> : null}
                      <span className="barra-aporte">
                        <span className="barra-aporte-relleno" style={{ width: `${regla.ancho}%` }} />
                      </span>
                    </td>
                    <td className="regla-peso">+{regla.peso}</td>
                    <td className="regla-peso">{regla.acumulado}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}>Suma de aportes</td>
                  <td className="regla-peso" id="suma-aportes">
                    {puntaje.suma}
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
            <div className="total-puntaje">
              <span>
                Puntaje total{puntaje.tope ? ` (la suma ${puntaje.suma} se acota al máximo ${puntaje.maximo})` : ""}
              </span>
              <span id="puntaje-total">
                {alerta.puntaje} <span aria-hidden="true">&rarr;</span> severidad {etiqueta(alerta.severidad).toLowerCase()}
              </span>
            </div>
          </>
        ) : (
          <>
            <div className="mensaje mensaje-error" role="note" id="aviso-v1">
              {detalle.aviso_v1}
            </div>
            <p>
              Reglas disparadas:{" "}
              {alerta.reglas_disparadas.map((regla, i) => (
                <span key={String(regla)}>
                  <span className="id-corto">{String(regla)}</span>
                  {i < alerta.reglas_disparadas.length - 1 ? ", " : ""}
                </span>
              ))}
            </p>
          </>
        )}

        <h3 className="seccion-titulo">Política y cortes de severidad</h3>
        {politica.origen === "usada" && politica.politica_id ? (
          <p>
            Política usada por la alerta: <span className="id-corto">{politica.politica_id}</span> versión {politica.version}.
          </p>
        ) : (
          <p>Política usada por la alerta: no registrada{detalle.es_v2 ? "" : " (alerta v1)"}.</p>
        )}
        {politica.origen !== "ninguna" ? (
          <>
            <p className="nota-tecnica" id="origen-cortes">
              {politica.origen === "usada"
                ? "Los cortes de abajo son los de la política usada por la alerta."
                : `Los cortes de la política usada no están disponibles: se muestran los de la política vigente ahora (${politica.politica_id ?? ""} versión ${politica.version ?? ""}), que pueden diferir de los que se aplicaron.`}
            </p>
            <div className="barra-puntaje" role="img" aria-label={`Puntaje ${alerta.puntaje} de ${puntaje.maximo}`}>
              <span className="barra-puntaje-relleno" style={{ width: `${puntaje.ancho_puntaje}%` }} />
              {puntaje.cortes.map((corte) => (
                <span
                  key={corte.severidad}
                  className="barra-puntaje-corte"
                  style={{ left: `${corte.inicio}%` }}
                  title={`${etiqueta(corte.severidad)} desde ${corte.minimo}`}
                />
              ))}
            </div>
            <table className="tabla-aportes" id="tabla-cortes">
              <thead>
                <tr>
                  <th scope="col">Severidad</th>
                  <th scope="col">Desde</th>
                  <th scope="col">Hasta</th>
                  <th scope="col">
                    <span className="visualmente-oculto">Corte alcanzado</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {puntaje.cortes.map((corte) => (
                  <tr key={corte.severidad} className={corte.alcanzado ? "corte-alcanzado" : undefined}>
                    <td>{etiqueta(corte.severidad)}</td>
                    <td>{corte.minimo}</td>
                    <td>{corte.maximo}</td>
                    <td>{corte.alcanzado ? `corte alcanzado (${alerta.puntaje})` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="nota-tecnica">
              Umbral de creación de alerta: {puntaje.umbral}. Puntaje máximo: {puntaje.maximo}.
            </p>
          </>
        ) : (
          <p className="nota-tecnica">No hay política vigente en la base.</p>
        )}
      </section>

      <Resolver detalle={detalle} />
    </>
  );
}

/** Resolver la alerta (con confirmacion si cierra) e historial. */
function Resolver({ detalle }: { detalle: DetalleAlerta }) {
  const { alerta } = detalle;
  const clienteConsultas = useQueryClient();
  const { data: sesion } = useSesion();
  const [nota, setNota] = useState("");
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const [pendiente, setPendiente] = useState<AccionDisponible | null>(null);
  const nombres = detalle.nombres_agentes;

  const transicion = useMutation({
    mutationFn: (accion: AccionDisponible) =>
      pedir<ResultadoTransicion>(`/api/v1/alertas/${encodeURIComponent(alerta._id)}/transiciones`, {
        metodo: "POST",
        cuerpo: { destino: accion.destino, nota: nota.trim(), estado_esperado: alerta.estado },
      }),
    onSuccess: (_resultado, accion) => {
      setNota("");
      setAviso({ texto: `Listo: la alerta pasó a ${etiqueta(accion.destino)}`, error: false });
      void clienteConsultas.invalidateQueries({ queryKey: claveDetalle(alerta._id) });
    },
    onError: (fallo) => {
      const conflicto = fallo instanceof ErrorApi && fallo.estado === 409;
      setAviso({
        texto: conflicto
          ? `La alerta cambió antes de que aplicaras la acción: otro agente la tomó o resolvió, o la acción ya no es válida desde su estado actual. Se muestra el estado vigente, sin cambios tuyos. Detalle: ${fallo.message}`
          : fallo instanceof ErrorApi
            ? `${fallo.estado}: ${fallo.message}`
            : "No se pudo aplicar la acción",
        error: true,
      });
      // Lo que se ve tiene que ser lo que hay ahora, no lo que se veia.
      void clienteConsultas.invalidateQueries({ queryKey: claveDetalle(alerta._id) });
    },
  });

  function elegir(accion: AccionDisponible) {
    setAviso(null);
    if (accion.cierra) setPendiente(accion);
    else transicion.mutate(accion);
  }

  function resolver(confirmada: boolean) {
    const accion = pendiente;
    setPendiente(null);
    if (confirmada && accion) transicion.mutate(accion);
  }

  const yo = sesion?.agente_id;
  return (
    <div id="acciones">
      <section className="tarjeta" aria-label="Resolver alerta">
        <h2 className="seccion-titulo">Resolver alerta</h2>
        <div aria-live="polite">
          {aviso ? (
            <div className={`mensaje ${aviso.error ? "mensaje-error" : "mensaje-ok"}`} role={aviso.error ? "alert" : "status"} id="aviso-resolver">
              {aviso.texto}
            </div>
          ) : null}
        </div>
        {detalle.caso_id ? (
          <p className="mensaje mensaje-ok" id="caso-creado">
            Caso abierto: <span className="id-corto">{detalle.caso_id}</span> (en investigación)
          </p>
        ) : null}
        {detalle.acciones.length > 0 ? (
          <>
            <div id="form-resolver" className="form-regla">
              {yo ? (
                <p className="nota-tecnica" id="actuas-como">
                  Actúas como{" "}
                  <strong>
                    {yo}
                    {nombres[yo] ? ` (${nombres[yo]})` : ""}
                  </strong>
                  : tu nombre queda en el historial.
                </p>
              ) : null}
              <div className="campo">
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
            </div>
            <div className="grupo-botones">
              {detalle.acciones.map((accion) => (
                <div className="accion-boton" key={accion.destino}>
                  <button
                    type="button"
                    className={`boton ${CLASE_BOTON[accion.destino] ?? ""}`}
                    data-destino={accion.destino}
                    aria-describedby={`ayuda-${accion.destino}`}
                    disabled={transicion.isPending}
                    onClick={() => elegir(accion)}
                  >
                    {accion.rotulo}
                  </button>
                  <small id={`ayuda-${accion.destino}`} className="ayuda-boton">
                    {accion.ayuda}
                  </small>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="nota-tecnica">
            La alerta está {etiqueta(alerta.estado).toLowerCase()}: es un estado terminal y no admite más acciones.
          </p>
        )}
      </section>

      <section className="tarjeta" aria-label="Historial de acciones">
        <h2 className="seccion-titulo">Historial de acciones</h2>
        <ol className="historial">
          {alerta.historial.map((accion, i) => (
            <li className="historial-item" key={`${accion.fecha}-${i}`}>
              <div className="cuando">{horaCostaRica(accion.fecha)} (hora CR)</div>
              <div className="quien">
                {accion.agente_id ? `${nombres[accion.agente_id] ?? ""} ${accion.agente_id}`.trim() : "Sistema"}
              </div>
              <div>
                Acción: <strong>{etiqueta(accion.accion)}</strong>
              </div>
              {accion.nota ? <div className="nota">&quot;{accion.nota}&quot;</div> : null}
            </li>
          ))}
        </ol>
      </section>

      <DialogoConfirmacion
        abierto={pendiente !== null}
        titulo={pendiente ? `${pendiente.rotulo}: confirmar` : "Confirmar acción"}
        etiquetaConfirmar={pendiente?.rotulo ?? "Confirmar"}
        alResolver={resolver}
      >
        {pendiente ? (
          <p>
            ¿{pendiente.rotulo}? Esta acción cierra la alerta {alerta._id} y no se puede deshacer.
            {nota.trim() ? ` Nota: «${nota.trim()}».` : ""}
          </p>
        ) : null}
      </DialogoConfirmacion>
    </div>
  );
}
