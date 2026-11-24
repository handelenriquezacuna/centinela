import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";

import { cerrarSesion, puedeAbrir, useSesion } from "../api/sesion";

/**
 * Encabezado y navegacion del portal, con las mismas clases que la plantilla HTMX
 * (`encabezado`, `nav-portal`, `sesion-agente`), asi que `centinela.css` los viste igual.
 * La navegacion sale de la sesion (GET /api/v1/sesion), no de una lista propia.
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
      <header className="encabezado">
        <Link className="marca" to="/panel">
          Centinela <span className="marca-sufijo">portal de fraude</span>
        </Link>
        {sesion ? (
          <>
            <nav className="nav-portal" aria-label="Navegación principal">
              {sesion.navegacion.map((pagina) => (
                <NavLink key={pagina.ruta} to={pagina.ruta}>
                  {pagina.rotulo}
                </NavLink>
              ))}
              {puedeAbrir(sesion, "/red-mulas") ? <NavLink to="/red-mulas">Red de mulas</NavLink> : null}
            </nav>
            <div className="sesion-agente">
              <span id="sesion-quien">
                <strong>{sesion.agente_id}</strong> ({sesion.rol})
              </span>
              <button type="button" className="boton boton-secundario" onClick={() => void salir()}>
                Salir
              </button>
            </div>
          </>
        ) : null}
      </header>
      <main id="contenido" className="contenedor" tabIndex={-1} ref={principal}>
        <Outlet />
      </main>
    </>
  );
}
