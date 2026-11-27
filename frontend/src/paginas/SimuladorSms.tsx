import { useQuery } from "@tanstack/react-query";

import { ErrorApi, pedir } from "../api/cliente";
import type { BandejaSms, MensajeSms } from "../api/tiposCliente";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { VistaError } from "../componentes/VistaError";
import { VistaVacia } from "../componentes/VistaVacia";
import { mensajeDeError } from "../reglas/errores";

const ESTADO_ENLACE = { vigente: "enlace vigente", usado: "enlace usado", vencido: "enlace vencido" } as const;

function partes(mensaje: MensajeSms) {
  const texto = mensaje.texto ?? "";
  const enlace = mensaje.ruta_enlace;
  const i = enlace ? texto.indexOf("http") : -1;
  return i >= 0 ? { antes: texto.slice(0, i), url: texto.slice(i) } : { antes: texto, url: null };
}

/**
 * Bandeja de un telefono simulado (T-82, demo): los SMS que D2 manda al cliente, con su enlace
 * clicable. Solo existe con `aviso_cliente.simulador_sms` (por omision, en dev) y la ven el
 * supervisor y el administrador. Es la forma de recorrer el flujo completo sin un proveedor real.
 */
export function SimuladorSms() {
  const consulta = useQuery({
    queryKey: ["simulador-sms"],
    queryFn: () => pedir<BandejaSms>("/api/v1/simulador-sms"),
    refetchInterval: 3000,
    retry: false,
  });
  const apagado = consulta.error instanceof ErrorApi && consulta.error.estado === 404;
  const mensajes = consulta.data?.mensajes ?? [];

  return (
    <MarcoPagina id="simulador-sms">
      <link rel="stylesheet" href="/estaticos/cliente.css" />
      <EncabezadoPagina
        id="simulador-sms"
        titulo="Simulador de teléfono"
        descripcion="Los SMS que el sistema manda al cliente cuando una alerta es crítica. Solo para la demo: no sale ningún mensaje real."
      />
      {apagado ? (
        <VistaVacia titulo="El simulador está desactivado en este entorno" texto="Se activa con aviso_cliente.simulador_sms (por omisión, solo en desarrollo)." />
      ) : consulta.isError ? (
        <VistaError error={mensajeDeError(consulta.error, "La bandeja")} alReintentar={() => void consulta.refetch()} />
      ) : (
        <div className="telefono" id="telefono" aria-label="Bandeja del teléfono simulado">
          <div className="telefono-barra">Mensajes</div>
          {mensajes.length === 0 ? <p className="telefono-vacio">Sin mensajes todavía.</p> : null}
          {mensajes.map((m) => {
            const { antes, url } = partes(m);
            return (
              <article className="sms" key={m.notificacion_id} data-alerta={m.alerta_id}>
                <header className="sms-cabecera">
                  <span>Para {m.para}</span>
                  <span>
                    <FechaCR valor={m.fecha} />
                  </span>
                </header>
                {m.texto ? (
                  <p className="sms-texto">
                    {antes}
                    {url && m.ruta_enlace ? (
                      <a href={m.ruta_enlace} target="_blank" rel="noreferrer" className="sms-enlace">
                        {url}
                      </a>
                    ) : null}
                  </p>
                ) : (
                  <p className="sms-texto">(sin texto: envío {m.estado_envio})</p>
                )}
                <footer className="sms-pie">
                  <span>{m.alerta_id}</span>
                  {m.enlace ? <span>{ESTADO_ENLACE[m.enlace]}</span> : null}
                  {m.respuesta ? <span>respondió: {m.respuesta.valor === "no_fui_yo" ? "no fui yo" : "sí fui yo"}</span> : null}
                </footer>
              </article>
            );
          })}
        </div>
      )}
    </MarcoPagina>
  );
}
