import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import { ErrorApi } from "../api/cliente";
import { CLAVE_SESION, iniciarSesion, useSesion } from "../api/sesion";
import { AvisoRegion } from "../componentes/AvisoRegion";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FormularioCampo } from "../componentes/FormularioCampo";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { destinoSeguro } from "../destino";

/**
 * Iniciar sesion. Mismos rotulos que la pantalla HTMX ("Identificador", "Contraseña",
 * "Entrar"): las pruebas e2e los buscan por etiqueta accesible. Al entrar se vuelve a
 * `?siguiente=` si es una ruta interna (`destinoSeguro`); sin el, a la pagina de inicio del rol.
 */
export function Login() {
  const [parametros] = useSearchParams();
  const pedido = parametros.get("siguiente");
  // Con `?siguiente=` se vuelve ahi; sin el, `/` manda a cada rol a su pantalla de inicio.
  const destino = pedido ? destinoSeguro(pedido) : "/";
  const vencida = parametros.get("motivo") === "vencida";
  const navegar = useNavigate();
  const clienteConsultas = useQueryClient();
  const { data: sesion } = useSesion();
  const [agenteId, setAgenteId] = useState("");
  const [contrasena, setContrasena] = useState("");
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  if (sesion) {
    return <Navigate to={destino} replace />;
  }

  async function entrar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    setEnviando(true);
    setMensaje(null);
    try {
      await iniciarSesion(agenteId.trim(), contrasena);
      await clienteConsultas.invalidateQueries({ queryKey: CLAVE_SESION });
      navegar(destino, { replace: true });
    } catch (fallo) {
      setContrasena("");
      setMensaje(fallo instanceof ErrorApi ? fallo.message : "No se pudo iniciar sesión");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <MarcoPagina id="login">
      <EncabezadoPagina id="login" titulo="Iniciar sesión" descripcion="Entre con su identificador y su contraseña para ver las alertas." />
      <section className="tarjeta tarjeta-acceso">
        <AvisoRegion
          resultado={
            mensaje
              ? { tipo: "error", error: { texto: mensaje, detalles: [] } }
              : vencida
                ? { tipo: "error", error: { texto: "Tu sesión venció. Entra de nuevo para seguir donde estabas.", detalles: [] } }
                : null
          }
        />
        <form className="form-regla form-acceso" onSubmit={(e) => void entrar(e)}>
          <FormularioCampo etiqueta="Identificador">
            <input
              type="text"
              name="agente_id"
              value={agenteId}
              onChange={(e) => setAgenteId(e.target.value)}
              placeholder="AGT-001"
              autoComplete="username"
              required
              autoFocus
            />
          </FormularioCampo>
          <FormularioCampo etiqueta="Contraseña">
            <input
              type="password"
              name="contrasena"
              value={contrasena}
              onChange={(e) => setContrasena(e.target.value)}
              autoComplete="current-password"
              required
            />
          </FormularioCampo>
          <div className="grupo-botones">
            <button type="submit" className="boton boton-primario" disabled={enviando}>
              Entrar
            </button>
          </div>
        </form>
      </section>
    </MarcoPagina>
  );
}
