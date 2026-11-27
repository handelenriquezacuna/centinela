import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router";

import { cerrarSesion, useSesion } from "../api/sesion";
import { AvisoSesion } from "./AvisoSesion";
import { MenuPrincipal } from "./MenuPrincipal";

/**
 * Encabezado y navegacion del portal, con las mismas clases que la plantilla HTMX
 * (`encabezado`, `nav-portal`, `sesion-agente`), asi que `centinela.css` los viste igual.
 * La navegacion sale de la sesion (GET /api/v1/sesion), no de una lista propia; el logotipo
 * lleva a la pagina de inicio del rol (`sesion.inicio`).
 *
 * Accesibilidad: al cambiar de pantalla el foco pasa al contenido principal, para que un
 * lector de pantalla no quede en el enlace que se pulso.
 */
export function Marco() {
  const { data: sesion } = useSesion();
  const ubicacion = useLocation();
  const navegar = useNavigate();
  const clienteConsultas = useQueryClient();
  const principal = useRef<HTMLElement>(null);
  const primera = useRef(true);

  useEffect(() => {
    if (primera.current) {
      primera.current = false;
      return;
    }
    principal.current?.focus();
  }, [ubicacion.pathname]);

  async function salir() {
    try {
      await cerrarSesion();
    } finally {
      clienteConsultas.clear();
      navegar("/login", { replace: true });
    }
  }

  return (
    <>
      <a className="saltar" href="#contenido">
        Saltar al contenido
      </a>
      <header className="encabezado">
        <Link className="marca" to={sesion?.inicio ?? "/login"}>
          Centinela <span className="marca-sufijo">portal de fraude</span>
        </Link>
        {sesion ? (
          <>
            <MenuPrincipal sesion={sesion} />
            <div className="sesion-agente">
              <span id="sesion-quien">
                <strong title={sesion.agente_id}>{sesion.nombre ?? sesion.agente_id}</strong> ({sesion.rol})
              </span>
              <button type="button" className="boton boton-secundario" onClick={() => void salir()}>
                Salir
              </button>
            </div>
          </>
        ) : null}
      </header>
      {sesion ? <AvisoSesion sesion={sesion} /> : null}
      <main id="contenido" className="contenedor" tabIndex={-1} ref={principal}>
        <Outlet />
      </main>
    </>
  );
}
