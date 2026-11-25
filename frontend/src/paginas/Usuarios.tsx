import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { MarcoPagina } from "../componentes/MarcoPagina";

/**
 * Usuarios del portal, solo lectura (D-79): el portal todavia no tiene API para dar de alta
 * o de baja; se hace con la linea de comandos y aqui se dice como.
 */
export function Usuarios() {
  return (
    <MarcoPagina id="usuarios">
      <EncabezadoPagina
        id="usuarios"
        titulo="Usuarios del portal"
        descripcion="Quién puede entrar y con qué rol. La administración de usuarios se hace por línea de comandos."
      />
      <section className="tarjeta" aria-labelledby="titulo-usuarios-comando">
        <h2 id="titulo-usuarios-comando" className="seccion-titulo">
          Cómo administrarlos
        </h2>
        <p>
          Desde la carpeta del proyecto, con el servidor detenido o en marcha (no lo necesita), pida la ayuda del comando:
        </p>
        <pre className="comando">
          <code>python tareas.py usuarios --help</code>
        </pre>
        <p className="nota-tecnica">
          Los roles son agente, supervisor y administrador. Los permisos de cada uno están en la tabla única de
          permisos del servidor.
        </p>
      </section>
    </MarcoPagina>
  );
}
