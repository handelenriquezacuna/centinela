import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { ErrorApi, pedir } from "../api/cliente";
import type { EnlaceDelCliente } from "../api/tiposCliente";
import { DialogoConfirmacion } from "../componentes/DialogoConfirmacion";
import { colones, horaCostaRica } from "../formatos";

type Valor = "si_fui_yo" | "no_fui_yo";

const TERMINALES = new Set(["fraude_confirmado", "sin_fraude", "en_investigacion"]);
const AYUDA_SIN_DATOS = "800-000-0000";

function ayudaDeError(error: unknown): string {
  return error instanceof ErrorApi ? (error.detalles.find((d) => d.campo === "telefono_ayuda")?.mensaje ?? AYUDA_SIN_DATOS) : AYUDA_SIN_DATOS;
}

function Telefono({ numero, ejemplo }: { numero: string; ejemplo: boolean }) {
  return (
    <strong className="cliente-telefono">
      {numero}
      {ejemplo ? <span className="cliente-ejemplo"> (número de ejemplo)</span> : null}
    </strong>
  );
}

/**
 * La pagina del cliente (T-82, D-82): publica, pensada para el telefono. El enlace del SMS
 * (`/c/{token}`) es la autorizacion; no hay sesion, menu ni enlaces al portal del banco.
 *
 * Antes de responder: la transferencia en palabras simples y dos botones grandes, cada uno con
 * confirmacion. Despues: el estado de su caso EN VIVO. Se hace con sondeo corto
 * (`sondeo_segundos` del servidor) y no con SSE: un canal SSE sin sesion dejaria a cualquiera
 * abrir change streams en la base. El sondeo se detiene cuando el caso llega a un estado final.
 * Enlace vencido, usado hace mucho o inexistente: la misma pagina, con el numero de ayuda.
 */
export function Cliente() {
  const { token = "" } = useParams();
  const clienteConsultas = useQueryClient();
  const [pendiente, setPendiente] = useState<Valor | null>(null);
  const [fallo, setFallo] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Tu transferencia · Banco";
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);

  const clave = ["cliente", token] as const;
  const consulta = useQuery({
    queryKey: clave,
    queryFn: () => pedir<EnlaceDelCliente>(`/api/v1/cliente/${encodeURIComponent(token)}`, { sinAvisoDeSesion: true }),
    retry: false,
    refetchInterval: (q) => {
      const dato = q.state.data;
      if (!dato || dato.fase !== "respondida" || (dato.caso && TERMINALES.has(dato.caso.codigo))) return false;
      return dato.sondeo_segundos * 1000;
    },
  });

  const responder = useMutation({
    mutationFn: (valor: Valor) =>
      pedir<EnlaceDelCliente>(`/api/v1/cliente/${encodeURIComponent(token)}/respuesta`, {
        metodo: "POST",
        cuerpo: { valor },
        sinAvisoDeSesion: true,
      }),
    onSuccess: (dato) => {
      setFallo(null);
      clienteConsultas.setQueryData(clave, dato);
    },
    onError: (error) => {
      setFallo(error instanceof ErrorApi ? error.message : "No pudimos registrar tu respuesta. Intenta de nuevo.");
      void consulta.refetch();
    },
  });

  const dato = consulta.data;
  let contenido;
  if (consulta.isPending) {
    contenido = (
      <p role="status" className="cliente-cargando">
        Cargando…
      </p>
    );
  } else if (consulta.isError || !dato) {
    const limitado = consulta.error instanceof ErrorApi && consulta.error.estado === 429;
    contenido = (
      <section className="cliente-tarjeta" id="cliente-no-valido" aria-labelledby="cliente-titulo-error">
        <h1 id="cliente-titulo-error">{limitado ? "Demasiados intentos" : "Este enlace ya no sirve"}</h1>
        <p>
          {limitado
            ? "Espera un momento antes de volver a intentar."
            : "El enlace no es válido o ya venció. Por tu seguridad, los enlaces duran poco tiempo."}
        </p>
        <p>
          Si necesitas ayuda, llama al <Telefono numero={ayudaDeError(consulta.error)} ejemplo />.
        </p>
      </section>
    );
  } else {
    const t = dato.transferencia;
    contenido = (
      <>
        <section className="cliente-tarjeta" aria-labelledby="cliente-titulo">
          <h1 id="cliente-titulo">{dato.fase === "pendiente" ? "¿Fuiste tú?" : "Tu transferencia"}</h1>
          <p className="cliente-monto" id="cliente-monto">
            {colones(t.monto_crc)}
          </p>
          <dl className="cliente-datos">
            <div>
              <dt>Cuándo</dt>
              <dd>{horaCostaRica(t.fecha)} (hora de Costa Rica)</dd>
            </div>
            <div>
              <dt>Desde tu cuenta</dt>
              <dd>terminada en {t.cuenta_terminada_en}</dd>
            </div>
            <div>
              <dt>Hacia</dt>
              <dd>{t.destino_enmascarado}</dd>
            </div>
          </dl>
          <p className="cliente-tipo">Transferencia SINPE Móvil</p>
        </section>

        {dato.fase === "pendiente" && dato.puede_responder ? (
          <section className="cliente-tarjeta" aria-label="Tu respuesta">
            <p className="cliente-pregunta">¿Hiciste esta transferencia?</p>
            <div className="cliente-botones">
              <button type="button" className="cliente-boton cliente-si" disabled={responder.isPending} onClick={() => setPendiente("si_fui_yo")}>
                Sí, fui yo
              </button>
              <button type="button" className="cliente-boton cliente-no" disabled={responder.isPending} onClick={() => setPendiente("no_fui_yo")}>
                No fui yo
              </button>
            </div>
            {fallo ? (
              <p className="cliente-error" role="alert">
                {fallo}
              </p>
            ) : null}
            <p className="cliente-nota">El enlace vale hasta las {horaCostaRica(dato.vence).slice(11)} y solo se puede usar una vez.</p>
          </section>
        ) : null}

        {dato.fase === "pendiente" && !dato.puede_responder && !dato.caso ? (
          <section className="cliente-tarjeta" aria-label="Enlace sin respuesta posible">
            <p>Este enlace ya no admite respuesta.</p>
          </section>
        ) : null}

        {dato.respuesta ? (
          <p className="cliente-respuesta" id="cliente-respuesta">
            Tu respuesta: <strong>{dato.respuesta.valor === "no_fui_yo" ? "No fui yo" : "Sí, fui yo"}</strong>
          </p>
        ) : null}

        {dato.caso ? (
          <section
            className={`cliente-tarjeta cliente-caso es-${dato.caso.codigo}`}
            aria-label="Estado de tu caso"
            aria-live="polite"
            id="cliente-caso"
            data-caso={dato.caso.codigo}
          >
            <h2>{dato.caso.titulo}</h2>
            <p>{dato.caso.detalle}</p>
          </section>
        ) : null}

        <p className="cliente-ayuda">
          ¿Necesitas ayuda? Llama al <Telefono numero={dato.ayuda.telefono} ejemplo={dato.ayuda.es_ejemplo} />.
        </p>

        <DialogoConfirmacion
          abierto={pendiente !== null}
          titulo={pendiente === "no_fui_yo" ? "Confirmar: no fui yo" : "Confirmar: sí fui yo"}
          etiquetaConfirmar={pendiente === "no_fui_yo" ? "Sí, no fui yo" : "Sí, fui yo"}
          alResolver={(confirmada) => {
            const valor = pendiente;
            setPendiente(null);
            if (confirmada && valor) responder.mutate(valor);
          }}
        >
          <p>
            {pendiente === "no_fui_yo"
              ? "Vamos a avisar a un agente de inmediato de que no hiciste esta transferencia. ¿Es correcto?"
              : "Vamos a anotar que hiciste esta transferencia. ¿Es correcto?"}
          </p>
          <p className="cliente-nota">Solo puedes responder una vez.</p>
        </DialogoConfirmacion>
      </>
    );
  }

  return (
    <main className="cliente" id="cliente">
      <link rel="stylesheet" href="/estaticos/cliente.css" />
      <p className="cliente-marca">Banco · Aviso de seguridad</p>
      {contenido}
    </main>
  );
}
