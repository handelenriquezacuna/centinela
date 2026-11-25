import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ErrorApi, pedir } from "../api/cliente";
import { useSesion } from "../api/sesion";
import type { Alerta, Pagina } from "../api/tipos";
import type { AccionDisponible, DetalleAlerta, ResultadoTransicion } from "../api/tiposDetalle";
import { BarraDecision } from "../componentes/BarraDecision";
import { Cargando } from "../componentes/Cargando";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { MontoColones } from "../componentes/MontoColones";
import { RecorridoDinero } from "../componentes/RecorridoDinero";
import { VistaError } from "../componentes/VistaError";
import type { ResultadoAviso } from "../componentes/AvisoRegion";
import { etiqueta } from "../formatos";
import { CLAVE_COLA } from "./PanelAlertas";

const ESTADOS_ABIERTOS: ReadonlySet<string> = new Set(["nueva", "en_revision"]);

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
    return <Cargando texto="Cargando la alerta…" />;
  }
  if (consulta.isError) {
    const noExiste = consulta.error instanceof ErrorApi && consulta.error.estado === 404;
    return (
      <MarcoPagina id="detalle">
        <EncabezadoPagina
          id="detalle"
          titulo={`Alerta ${alertaId}`}
          migas={[{ rotulo: "Panel", a: "/panel" }, { rotulo: `Alerta ${alertaId}` }]}
        />
        <VistaError
          error={{
            texto: noExiste ? `No existe la alerta ${alertaId}.` : `No se pudo cargar la alerta: ${consulta.error.message}`,
            detalles: [],
          }}
          alReintentar={noExiste ? undefined : () => void consulta.refetch()}
        />
      </MarcoPagina>
    );
  }
  return <DetalleCargado key={alertaId} detalle={consulta.data} />;
}

/**
 * La alerta que sigue en la cola, en el orden del servidor (puntaje y fecha), sin contar
 * las ya cerradas ni la actual; tras la ultima vuelve a la primera. `undefined` mientras se lee;
 * `null` si no queda ninguna.
 */
function useSiguienteAlerta(actual: string): string | null | undefined {
  const cola = useQuery({ queryKey: CLAVE_COLA, queryFn: () => pedir<Pagina<Alerta>>("/api/v1/alertas") });
  if (cola.isPending) return undefined;
  if (cola.isError) return null;
  const abiertas = cola.data.elementos.filter((a) => ESTADOS_ABIERTOS.has(a.estado) || a._id === actual);
  const posicion = abiertas.findIndex((a) => a._id === actual);
  // Las que siguen a la actual; si era la ultima, la cola vuelve a empezar por la mas urgente.
  const ordenadas = posicion === -1 ? abiertas : [...abiertas.slice(posicion + 1), ...abiertas.slice(0, posicion)];
  return ordenadas.find((a) => a._id !== actual && ESTADOS_ABIERTOS.has(a.estado))?._id ?? null;
}

function DetalleCargado({ detalle }: { detalle: DetalleAlerta }) {
  const { alerta, transaccion, cliente, perfil, puntaje, politica } = detalle;
  const nombres = detalle.nombres_agentes;
  const nombreAgente = alerta.agente_id ? nombres[alerta.agente_id] : undefined;
  const instante = transaccion?.fecha ?? alerta.fecha_creacion;
  const consultaRed = `?desde=${encodeURIComponent(instante)}`;
  const decision = useResolver(detalle);
  const siguiente = useSiguienteAlerta(alerta._id);
  return (
    <MarcoPagina id="detalle">
      <link rel="stylesheet" href="/estaticos/detalle.css" />
      <EncabezadoPagina
        id="detalle"
        titulo={`Alerta ${alerta._id}`}
        migas={[{ rotulo: "Panel", a: "/panel" }, { rotulo: `Alerta ${alerta._id}` }]}
        descripcion={
          <>
            Transacción <span className="id-corto">{alerta.transaccion_id}</span> · creada <FechaCR valor={alerta.fecha_creacion} formato="con_zona" />
          </>
        }
      />

      <BarraDecision
        alertaId={alerta._id}
        estado={alerta.estado}
        severidad={alerta.severidad}
        acciones={detalle.acciones}
        ocupado={decision.ocupado}
        alResolver={decision.resolver}
        resultado={decision.resultado}
        actuaComo={decision.actuaComo}
        siguiente={siguiente}
        casoId={detalle.caso_id}
      />

      <section className="tarjeta" aria-label="Resumen de la alerta">
        <div className="resumen-alerta">
          <div className="puntaje">
            {alerta.puntaje}
            <small>puntaje / {puntaje.maximo}</small>
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
            <dd>
              <MontoColones valor={alerta.monto_crc} />
            </dd>
          </div>
          {transaccion ? (
            <div>
              <dt>Canal</dt>
              <dd>{etiqueta(transaccion.canal)}</dd>
            </div>
          ) : null}
          <div>
            <dt>Fecha y hora (hora CR)</dt>
            <dd>
              <FechaCR valor={instante} />
            </dd>
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
                <dd>
                  <MontoColones valor={perfil.promedio_crc} />
                </dd>
              </div>
              <div>
                <dt>Monto máximo</dt>
                <dd>
                  <MontoColones valor={perfil.maximo_crc} />
                </dd>
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
          <details className="cortes-plegados">
            <summary>Ver los cortes de severidad</summary>
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
          </details>
        ) : (
          <p className="nota-tecnica">No hay política vigente en la base.</p>
        )}
      </section>

      <section className="tarjeta" aria-label="Historial de acciones">
        <h2 className="seccion-titulo">Historial de acciones</h2>
        <ol className="historial">
          {alerta.historial.map((accion, i) => (
            <li className="historial-item" key={`${accion.fecha}-${i}`}>
              <div className="cuando">
                <FechaCR valor={accion.fecha} formato="con_zona" />
              </div>
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
    </MarcoPagina>
  );
}

/**
 * Aplica una transicion de la maquina de estados con el estado que la persona vio
 * (`estado_esperado`): si otra persona se adelanto, el servidor responde conflicto, se
 * dice con palabras y la pantalla vuelve a leer lo vigente.
 */
function useResolver(detalle: DetalleAlerta) {
  const { alerta } = detalle;
  const clienteConsultas = useQueryClient();
  const { data: sesion } = useSesion();
  const [resultado, setResultado] = useState<ResultadoAviso>(null);
  const nombres = detalle.nombres_agentes;

  const transicion = useMutation({
    mutationFn: ({ accion, nota }: { accion: AccionDisponible; nota: string }) =>
      pedir<ResultadoTransicion>(`/api/v1/alertas/${encodeURIComponent(alerta._id)}/transiciones`, {
        metodo: "POST",
        cuerpo: { destino: accion.destino, nota, estado_esperado: alerta.estado },
      }),
    onSuccess: (_resultado, { accion }) => {
      setResultado({ tipo: "ok", texto: `Listo: la alerta pasó a ${etiqueta(accion.destino)}` });
      void clienteConsultas.invalidateQueries({ queryKey: claveDetalle(alerta._id) });
      void clienteConsultas.invalidateQueries({ queryKey: CLAVE_COLA });
    },
    onError: (fallo) => {
      const conflicto = fallo instanceof ErrorApi && fallo.estado === 409;
      setResultado({
        tipo: "error",
        error: {
          texto: conflicto
            ? `La alerta cambió antes de que aplicaras la acción: otro agente la tomó o resolvió, o la acción ya no es válida desde su estado actual. Se muestra el estado vigente, sin cambios tuyos. Detalle: ${fallo.message}`
            : fallo instanceof ErrorApi
              ? fallo.message
              : "No se pudo aplicar la acción",
          detalles: [],
        },
      });
      // Lo que se ve tiene que ser lo que hay ahora, no lo que se veia.
      void clienteConsultas.invalidateQueries({ queryKey: claveDetalle(alerta._id) });
    },
  });

  const yo = sesion?.agente_id;
  return {
    ocupado: transicion.isPending,
    resultado,
    actuaComo: yo ? `${yo}${nombres[yo] ? ` (${nombres[yo]})` : ""}` : null,
    resolver: (accion: AccionDisponible, nota: string) => {
      setResultado(null);
      transicion.mutate({ accion, nota });
    },
  };
}
