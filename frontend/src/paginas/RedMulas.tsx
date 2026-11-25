import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";

import { ErrorApi, pedir } from "../api/cliente";
import { useSesion } from "../api/sesion";
import type { EntradaListaRiesgo, ParametrosRed, RespuestaGrafo, ResultadoAprobacion } from "../api/tiposRed";
import { DialogoConfirmacion } from "../componentes/DialogoConfirmacion";
import { GrafoRed } from "../componentes/GrafoRed";
import { CLAVE_RED, RecorridoDinero } from "../componentes/RecorridoDinero";
import { AvisoRegion, type ResultadoAviso } from "../componentes/AvisoRegion";
import { Cargando } from "../componentes/Cargando";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { FormularioCampo } from "../componentes/FormularioCampo";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { MontoColones } from "../componentes/MontoColones";
import { Permitido } from "../componentes/Permitido";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { VistaError } from "../componentes/VistaError";
import { VistaVacia } from "../componentes/VistaVacia";
import { decimal } from "../formatos";
import { mensajeDeError } from "../reglas/errores";

const PATRON_CUENTA = /^(CTA-\d{3,}|\d{4}-\d{4})$/;

const COLUMNAS_ARISTAS: Columna<RespuestaGrafo["red"]["aristas"][number]>[] = [
  { clave: "salto", titulo: "Salto", celda: (a) => a.salto },
  { clave: "origen", titulo: "Origen", celda: (a) => a.origen, clase: "id-corto" },
  { clave: "destino", titulo: "Destino", celda: (a) => a.destino, clase: "id-corto" },
  { clave: "monto", titulo: "Monto", celda: (a) => <MontoColones valor={a.monto_crc} /> },
  { clave: "fecha", titulo: "Fecha (hora CR)", celda: (a) => <FechaCR valor={a.fecha} /> },
  {
    clave: "minutos",
    titulo: "Minutos desde el eslabón anterior",
    celda: (a) => (a.minutos_desde_anterior !== null ? decimal(a.minutos_desde_anterior) : "-"),
  },
];

function parametrosDe(buscar: URLSearchParams): ParametrosRed {
  return {
    desde: buscar.get("desde") ?? undefined,
    ventana_minutos: buscar.get("ventana_minutos") ?? undefined,
    saltos: buscar.get("saltos") ?? undefined,
  };
}

/**
 * Red de mulas (/red-mulas y /red-mulas/:cuenta): el Sankey con su marca de mula, el
 * grafo, la tabla de transferencias, las propuestas para `listas_riesgo` (aprobar) y las
 * entradas vigentes de la red (dar de baja, con confirmacion). Lo que depende de
 * permisos (aprobar, dar de baja, marcar) solo aparece con `gestionar_listas`; el servidor
 * lo vuelve a comprobar en cada peticion.
 */
export function RedMulas() {
  const { cuenta: deRuta } = useParams();
  const [buscar] = useSearchParams();
  const navegar = useNavigate();
  const cuenta = deRuta ?? buscar.get("cuenta") ?? "";
  const parametros = parametrosDe(buscar);
  const [escrita, setEscrita] = useState(cuenta);
  const [errorForm, setErrorForm] = useState<string | null>(null);

  function ver(evento: FormEvent) {
    evento.preventDefault();
    const limpia = escrita.trim();
    if (!PATRON_CUENTA.test(limpia)) {
      setErrorForm("La cuenta debe ser un número SINPE (6033-9001) o un CTA-001.");
      return;
    }
    setErrorForm(null);
    navegar(`/red-mulas/${encodeURIComponent(limpia)}`);
  }

  return (
    <MarcoPagina id="red" ancho="ancho">
      <link rel="stylesheet" href="/estaticos/tablero.css" />
      <EncabezadoPagina id="red" titulo="Red de cuentas mula" descripcion="Hacia dónde se movió el dinero desde una cuenta, hasta 6 saltos." />

      <form className="formulario-cuenta" onSubmit={ver} noValidate>
        <FormularioCampo etiqueta="Cuenta (SINPE 6033-9001 o CTA-001)" error={errorForm}>
          <input name="cuenta" value={escrita} required onChange={(e) => setEscrita(e.target.value)} />
        </FormularioCampo>
        <button className="boton boton-primario" type="submit">
          Ver red
        </button>
      </form>

      {cuenta ? <Red key={cuenta} cuenta={cuenta} parametros={parametros} /> : null}
    </MarcoPagina>
  );
}

function Red({ cuenta, parametros }: { cuenta: string; parametros: ParametrosRed }) {
  const clienteConsultas = useQueryClient();
  const { data: sesion } = useSesion();
  const puedeGestionar = sesion?.permisos.includes("gestionar_listas") ?? false;
  const [aviso, setAviso] = useState<ResultadoAviso>(null);
  const [baja, setBaja] = useState<EntradaListaRiesgo | null>(null);

  const consulta = {
    desde: parametros.desde,
    ventana_minutos: parametros.ventana_minutos,
    saltos: parametros.saltos,
  };
  const grafo = useQuery({
    queryKey: [...CLAVE_RED, "grafo", cuenta, consulta],
    queryFn: () => pedir<RespuestaGrafo>(`/api/v1/red-mulas/${encodeURIComponent(cuenta)}/grafo`, { consulta }),
  });

  const aprobar = useMutation({
    mutationFn: (propuesta: string) =>
      pedir<ResultadoAprobacion>(`/api/v1/red-mulas/${encodeURIComponent(cuenta)}/propuestas/${encodeURIComponent(propuesta)}`, {
        metodo: "POST",
      }),
    onSuccess: (resultado, propuesta) => {
      setAviso({
        tipo: "ok",
        texto: resultado.creada
          ? `La cuenta ${propuesta} se agregó a la lista de riesgo como ${resultado.entrada._id}.`
          : `La cuenta ${propuesta} ya estaba vigente como ${resultado.entrada._id}: no se duplicó.`,
      });
      void clienteConsultas.invalidateQueries({ queryKey: CLAVE_RED });
    },
    onError: (fallo, propuesta) => {
      setAviso({ tipo: "error", error: { texto: `No se agregó la cuenta ${propuesta}: ${fallo instanceof ErrorApi ? fallo.message : "falló la petición"}`, detalles: [] } });
    },
  });

  const darDeBaja = useMutation({
    mutationFn: (entrada: EntradaListaRiesgo) =>
      pedir<void>(`/api/v1/listas-riesgo/${encodeURIComponent(entrada._id)}`, { metodo: "DELETE" }),
    onSuccess: (_vacio, entrada) => {
      setAviso({ tipo: "ok", texto: `Dada de baja: ${entrada._id} ya no está en la lista.` });
      void clienteConsultas.invalidateQueries({ queryKey: CLAVE_RED });
    },
    onError: (fallo, entrada) => {
      setAviso({ tipo: "error", error: { texto: `No se dio de baja ${entrada._id}: ${fallo instanceof ErrorApi ? fallo.message : "falló la petición"}`, detalles: [] } });
      void clienteConsultas.invalidateQueries({ queryKey: CLAVE_RED });
    },
  });

  function resolverBaja(confirmada: boolean) {
    const entrada = baja;
    setBaja(null);
    if (confirmada && entrada) darDeBaja.mutate(entrada);
  }

  if (grafo.isPending) {
    return <Cargando texto="Cargando la red…" />;
  }
  if (grafo.isError) {
    return <VistaError error={mensajeDeError(grafo.error, "La red")} alReintentar={() => void grafo.refetch()} />;
  }
  const { red, entradas_lista: entradas } = grafo.data;
  const dibujo = grafo.data.grafo;
  return (
    <>
      <p className="nota-tecnica" id="red-resumen">
        Partida: <span className="id-corto">{red.cuenta}</span>
        {red.desde ? <> desde <FechaCR valor={red.desde} formato="con_zona" /></> : null}; ventana entre eslabones: {red.ventana_minutos} min; saltos
        recorridos: {red.saltos_recorridos} de {red.saltos_maximos}. {red.truncada ? "La red se truncó por tamaño." : ""}
      </p>

      <div aria-live="polite" id="red-aviso">
        <AvisoRegion resultado={aviso} />
      </div>

      {dibujo ? (
        <>
          <section className="tarjeta" aria-label="Recorrido del dinero">
            <h2 className="seccion-titulo">Recorrido del dinero (Sankey)</h2>
            <RecorridoDinero cuenta={cuenta} parametros={parametros} />
          </section>

          <section className="tarjeta grafo-contenedor" aria-label="Grafo">
            <GrafoRed grafo={dibujo} cuenta={red.cuenta} />
          </section>

          <section className="tarjeta" aria-label="Transferencias">
            <h2 className="seccion-titulo">Transferencias de la red</h2>
            <div id="tabla-aristas">
              <TablaDatos
                leyenda="Transferencias de la red, por salto"
                columnas={COLUMNAS_ARISTAS}
                filas={red.aristas}
                clave={(a) => a.transaccion_id}
                estado="listo"
              />
            </div>
          </section>
        </>
      ) : (
        <section className="tarjeta" id="red-sin-aristas">
          <VistaVacia
            titulo="Esta cuenta no tiene salidas registradas en la ventana."
            texto="No hay red que dibujar. Pruebe con otra cuenta, o revise el detalle de la alerta para ver desde qué fecha se consultó."
          />
        </section>
      )}

      <section className="tarjeta" aria-label="Propuestas">
        <h2 className="seccion-titulo">Cuentas propuestas para la lista de riesgo</h2>
        {red.propuestas_lista_riesgo.length > 0 ? (
          <>
            <ul id="propuestas">
              {red.propuestas_lista_riesgo.map((sinpe) => (
                <li className="id-corto" id={`propuesta-${sinpe}`} key={sinpe}>
                  {sinpe}{" "}
                  <Permitido permiso="gestionar_listas">
                    <button
                      type="button"
                      className="boton boton-primario"
                      disabled={aprobar.isPending}
                      aria-label={`Agregar ${sinpe} a lista de riesgo`}
                      onClick={() => {
                        setAviso(null);
                        aprobar.mutate(sinpe);
                      }}
                    >
                      Agregar a lista de riesgo
                    </button>
                  </Permitido>
                </li>
              ))}
            </ul>
            <p className="nota-tecnica">
              Son propuestas: el sistema no escribe en la lista por su cuenta.{" "}
              {puedeGestionar
                ? "Agregarlas es decisión tuya, con el botón de cada cuenta."
                : "Agregarlas es decisión de un supervisor o administrador."}
            </p>
          </>
        ) : (
          <div id="propuestas-vacias">
            <VistaVacia titulo="No hay cuentas por proponer." texto="Ninguna cuenta de esta red cumple el patrón de mula todavía." />
          </div>
        )}
      </section>

      {entradas.length > 0 ? (
        <section className="tarjeta" aria-label="Entradas en lista de riesgo">
          <h2 className="seccion-titulo">Entradas en lista de riesgo de esta red</h2>
          <ul id="entradas-lista">
            {entradas.map((entrada) => (
              <li id={`entrada-${entrada._id}`} key={entrada._id}>
                <span className="id-corto">{entrada.cuenta}</span> &middot; {entrada._id}
                {!entrada.vigente ? " (no vigente)" : ""}: {entrada.motivo}, reportada <FechaCR valor={entrada.fecha_reporte} formato="con_zona" />{" "}
                <Permitido permiso="gestionar_listas">
                  <button
                    type="button"
                    className="boton"
                    disabled={darDeBaja.isPending}
                    aria-label={`Dar de baja ${entrada._id} de ${entrada.cuenta}`}
                    onClick={() => setBaja(entrada)}
                  >
                    Dar de baja
                  </button>
                </Permitido>
              </li>
            ))}
          </ul>
          <p className="nota-tecnica">Dar de baja borra la entrada (por ejemplo, una cuenta reportada por error) y deja el rastro en el log.</p>
        </section>
      ) : null}

      <DialogoConfirmacion abierto={baja !== null} titulo="Dar de baja" etiquetaConfirmar="Dar de baja" alResolver={resolverBaja}>
        {baja ? (
          <p>
            Se borra la entrada {baja._id} de {baja.cuenta}. Queda registro en el log. ¿Continuar?
          </p>
        ) : null}
      </DialogoConfirmacion>
    </>
  );
}
