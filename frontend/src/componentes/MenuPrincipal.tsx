import { useEffect, useId, useState } from "react";
import { NavLink, useLocation } from "react-router";

import type { PaginaPermitida, SesionActual } from "../api/tipos";

/** Agrupa por area conservando el orden en que el servidor las entrega. */
export function agruparPorArea(paginas: PaginaPermitida[]): [string, PaginaPermitida[]][] {
  const grupos = new Map<string, PaginaPermitida[]>();
  for (const pagina of paginas) {
    grupos.set(pagina.area, [...(grupos.get(pagina.area) ?? []), pagina]);
  }
  return [...grupos.entries()];
}

/**
 * La navegacion principal: lo que el servidor dice que el rol puede abrir (`sesion.navegacion`),
 * agrupado por area. En pantalla ancha va en una fila; en telefono queda detras de un boton
 * "Menu" (una sola fila de cabecera) que se cierra al elegir una pantalla o con Esc.
 */
export function MenuPrincipal({ sesion }: { sesion: SesionActual }) {
  const idMenu = useId();
  const { pathname } = useLocation();
  // Abierto "en" una ruta: al cambiar de pantalla se cierra solo, sin efectos.
  const [abiertoEn, setAbiertoEn] = useState<string | null>(null);
  const abierto = abiertoEn === pathname;

  useEffect(() => {
    if (!abierto) return;
    const alTeclear = (evento: KeyboardEvent) => {
      if (evento.key === "Escape") setAbiertoEn(null);
    };
    document.addEventListener("keydown", alTeclear);
    return () => document.removeEventListener("keydown", alTeclear);
  }, [abierto]);

  return (
    <>
      <button
        type="button"
        className="menu-boton"
        aria-expanded={abierto}
        aria-controls={idMenu}
        onClick={() => setAbiertoEn(abierto ? null : pathname)}
      >
        Menú
      </button>
      <nav id={idMenu} className={abierto ? "nav-portal menu-portal menu-abierto" : "nav-portal menu-portal"} aria-label="Navegación principal">
        {agruparPorArea(sesion.navegacion).map(([area, paginas]) => (
          <div className="menu-grupo" role="group" aria-label={area} key={area}>
            <span className="menu-area" aria-hidden="true">
              {area}
            </span>
            {paginas.map((pagina) => (
              <NavLink key={pagina.ruta} to={pagina.ruta}>
                {pagina.rotulo}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
    </>
  );
}
