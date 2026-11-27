import { useQuery } from "@tanstack/react-query";

import { pedir } from "../api/cliente";
import type { ListaUsuarios, UsuarioPortal } from "../api/tipos";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { mensajeDeError } from "../reglas/errores";

const CLAVE_USUARIOS = ["usuarios"] as const;

function estadoDe(usuario: UsuarioPortal): string {
  if (usuario.bloqueado) return "Bloqueado";
  if (!usuario.activo) return "Inactivo";
  if (!usuario.tiene_credencial) return "Sin contraseña";
  return "Activo";
}

const COLUMNAS: Columna<UsuarioPortal>[] = [
  { clave: "agente_id", titulo: "Identificador", celda: (u) => u.agente_id },
  { clave: "nombre", titulo: "Nombre", celda: (u) => u.nombre },
  { clave: "rol", titulo: "Rol", celda: (u) => u.rol },
  { clave: "estado", titulo: "Estado", celda: (u) => estadoDe(u) },
  { clave: "intentos", titulo: "Intentos fallidos", celda: (u) => u.intentos_fallidos },
  {
    clave: "hasta",
    titulo: "Bloqueado hasta",
    celda: (u) => (u.bloqueado && u.bloqueado_hasta ? <FechaCR valor={u.bloqueado_hasta} formato="con_zona" /> : "—"),
  },
];

/**
 * Usuarios del portal, solo lectura (D-79, T-83): quien puede entrar, con que rol y cuales
 * cuentas estan bloqueadas por intentos fallidos. Dar de alta, de baja o desbloquear se
 * hace por linea de comandos; aqui se dice como.
 */
export function Usuarios() {
  const lista = useQuery({ queryKey: CLAVE_USUARIOS, queryFn: () => pedir<ListaUsuarios>("/api/v1/usuarios") });
  const bloqueados = lista.data?.bloqueados ?? 0;
  return (
    <MarcoPagina id="usuarios">
      <EncabezadoPagina
        id="usuarios"
        titulo="Usuarios del portal"
        descripcion="Quién puede entrar y con qué rol. La administración de usuarios se hace por línea de comandos."
      />
      <section className="tarjeta" aria-labelledby="titulo-usuarios-lista">
        <h2 id="titulo-usuarios-lista" className="seccion-titulo">
          Cuentas
        </h2>
        <p role="status">
          {lista.isSuccess ? (bloqueados === 0 ? "Ninguna cuenta está bloqueada." : `Cuentas bloqueadas: ${bloqueados}.`) : ""}
        </p>
        <TablaDatos
          leyenda="Usuarios del portal"
          columnas={COLUMNAS}
          filas={lista.data?.elementos ?? []}
          clave={(u) => u.agente_id}
          estado={lista.isPending ? "cargando" : lista.isError ? "error" : "listo"}
          error={lista.isError ? mensajeDeError(lista.error, "La lista de usuarios", false) : undefined}
          alReintentar={() => void lista.refetch()}
          atributosFila={(u) => ({ "data-bloqueado": u.bloqueado })}
        />
      </section>
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
        <p>Para desbloquear una cuenta antes de que pase el plazo:</p>
        <pre className="comando">
          <code>python tareas.py usuarios --agente AGT-001 --desbloquear</code>
        </pre>
        <p className="nota-tecnica">
          Los roles son agente, supervisor y administrador. Los permisos de cada uno están en la tabla única de
          permisos del servidor.
        </p>
      </section>
    </MarcoPagina>
  );
}
