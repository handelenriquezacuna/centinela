import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { createBrowserRouter, Navigate, RouterProvider, type RouteObject } from "react-router";

import { alPerderSesion, ErrorApi } from "./api/cliente";
import { CLAVE_SESION } from "./api/sesion";
import { Marco } from "./componentes/Marco";
import { RequiereSesion } from "./componentes/RequiereSesion";
import { Bitacora } from "./paginas/Bitacora";
import { Busqueda } from "./paginas/Busqueda";
import { Cliente } from "./paginas/Cliente";
import { Login } from "./paginas/Login";
import { NoEncontrada } from "./paginas/NoEncontrada";
import { Notificaciones } from "./paginas/Notificaciones";
import { Detalle } from "./paginas/Detalle";
import { Inicio } from "./paginas/Inicio";
import { Panel } from "./paginas/Panel";
import { RedMulas } from "./paginas/RedMulas";
import { Respaldos } from "./paginas/Respaldos";
import { Reglas } from "./paginas/Reglas";
import { Salud } from "./paginas/Salud";
import { SimuladorSms } from "./paginas/SimuladorSms";
import { Tablero } from "./paginas/Tablero";
import { Usuarios } from "./paginas/Usuarios";

/**
 * Cada pantalla declara la plantilla de su ruta tal como la tiene la tabla de permisos
 * (`/detalle/{alerta_id}`); `RequiereSesion` la compara con `sesion.paginas`. Todas
 * las pantallas ya estan migradas (fase 2: T-64, T-65 y T-66).
 */
function protegida(plantilla: string, elemento: ReactNode): ReactNode {
  return <RequiereSesion plantilla={plantilla}>{elemento}</RequiereSesion>;
}

export const RUTAS: RouteObject[] = [
  // T-82: la pagina publica del cliente va FUERA del marco del portal: sin menu, sin sesion
  // y sin enlaces a otras pantallas del banco. El token del enlace es la autorizacion.
  { path: "/c/:token", element: <Cliente /> },
  {
    element: <Marco />,
    children: [
      { path: "/", element: <Inicio /> },
      { path: "/login", element: <Login /> },
      { path: "/panel", element: protegida("/panel", <Panel />) },
      { path: "/detalle", element: <Navigate to="/panel" replace /> },
      {
        path: "/detalle/:alertaId",
        element: protegida("/detalle/{alerta_id}", <Detalle />),
      },
      {
        path: "/busqueda",
        element: protegida("/busqueda", <Busqueda />),
      },
      {
        path: "/red-mulas",
        element: protegida("/red-mulas", <RedMulas />),
      },
      {
        path: "/red-mulas/:cuenta",
        element: protegida("/red-mulas/{cuenta}", <RedMulas />),
      },
      {
        path: "/tablero",
        element: protegida("/tablero", <Tablero />),
      },
      {
        path: "/reglas",
        element: protegida("/reglas", <Reglas />),
      },
      { path: "/administracion", element: <Navigate to="/administracion/salud" replace /> },
      {
        path: "/administracion/salud",
        element: protegida("/administracion/salud", <Salud />),
      },
      {
        path: "/administracion/respaldos",
        element: protegida("/administracion/respaldos", <Respaldos />),
      },
      {
        path: "/administracion/usuarios",
        element: protegida("/administracion/usuarios", <Usuarios />),
      },
      {
        path: "/administracion/bitacora",
        element: protegida("/administracion/bitacora", <Bitacora />),
      },
      {
        path: "/notificaciones",
        element: protegida("/notificaciones", <Notificaciones />),
      },
      { path: "/simulador-sms", element: protegida("/simulador-sms", <SimuladorSms />) },
      { path: "*", element: <NoEncontrada /> },
    ],
  },
];

export function crearClienteConsultas(): QueryClient {
  const cliente = new QueryClient({
    defaultOptions: {
      queries: {
        // Un 4xx no se arregla reintentando; un fallo de red o un 5xx, quiza.
        retry: (intentos, fallo) => !(fallo instanceof ErrorApi && fallo.estado >= 400 && fallo.estado < 500) && intentos < 2,
        refetchOnWindowFocus: false,
      },
    },
  });
  // Un 401 en cualquier peticion: la sesion se acabo. `RequiereSesion` lleva al login.
  alPerderSesion(() => cliente.setQueryData(CLAVE_SESION, null));
  return cliente;
}

export function App() {
  // Uno por aplicacion, no por render.
  const [clienteConsultas] = useState(crearClienteConsultas);
  const [enrutador] = useState(() => createBrowserRouter(RUTAS));
  return (
    <QueryClientProvider client={clienteConsultas}>
      <RouterProvider router={enrutador} />
    </QueryClientProvider>
  );
}
