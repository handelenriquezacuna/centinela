import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { createBrowserRouter, Navigate, RouterProvider, type RouteObject } from "react-router";

import { alPerderSesion, ErrorApi } from "./api/cliente";
import { CLAVE_SESION } from "./api/sesion";
import { Marco } from "./componentes/Marco";
import { RequiereSesion } from "./componentes/RequiereSesion";
import { Busqueda } from "./paginas/Busqueda";
import { Login } from "./paginas/Login";
import { NoEncontrada } from "./paginas/NoEncontrada";
import { Detalle } from "./paginas/Detalle";
import { Panel } from "./paginas/Panel";
import { RedMulas } from "./paginas/RedMulas";
import { Reglas } from "./paginas/Reglas";
import { Tablero } from "./paginas/Tablero";

/**
 * Cada pantalla declara la plantilla de su ruta tal como la tiene la tabla de permisos
 * (`/detalle/{alerta_id}`); `RequiereSesion` la compara con `sesion.paginas`. Todas
 * las pantallas ya estan migradas (fase 2: T-64, T-65 y T-66).
 */
function protegida(plantilla: string, elemento: ReactNode): ReactNode {
  return <RequiereSesion plantilla={plantilla}>{elemento}</RequiereSesion>;
}

export const RUTAS: RouteObject[] = [
  {
    element: <Marco />,
    children: [
      { path: "/", element: <Navigate to="/panel" replace /> },
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
