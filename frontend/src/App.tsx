import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { createBrowserRouter, Navigate, RouterProvider, type RouteObject } from "react-router";

import { alPerderSesion, ErrorApi } from "./api/cliente";
import { CLAVE_SESION } from "./api/sesion";
import { Marco } from "./componentes/Marco";
import { RequiereSesion } from "./componentes/RequiereSesion";
import { Login } from "./paginas/Login";
import { NoEncontrada } from "./paginas/NoEncontrada";
import { Panel } from "./paginas/Panel";
import { Pendiente } from "./paginas/Pendiente";

/**
 * Cada pantalla declara la plantilla de su ruta tal como la tiene la tabla de permisos
 * (`/detalle/{alerta_id}`); `RequiereSesion` la compara con `sesion.paginas`. Las que
 * dicen `Pendiente` las migra la fase 2, cada una con el endpoint que va a leer.
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
        element: protegida(
          "/detalle/{alerta_id}",
          <Pendiente titulo="Detalle de alerta" endpoint="GET /api/v1/alertas/{alerta_id}/detalle" />,
        ),
      },
      {
        path: "/busqueda",
        element: protegida("/busqueda", <Pendiente titulo="Búsqueda forense" endpoint="GET /api/v1/transacciones" />),
      },
      {
        path: "/red-mulas",
        element: protegida("/red-mulas", <Pendiente titulo="Red de mulas" endpoint="GET /api/v1/red-mulas/{cuenta}/grafo" />),
      },
      {
        path: "/red-mulas/:cuenta",
        element: protegida(
          "/red-mulas/{cuenta}",
          <Pendiente titulo="Red de mulas" endpoint="GET /api/v1/red-mulas/{cuenta}/grafo y /sankey" />,
        ),
      },
      {
        path: "/tablero",
        element: protegida("/tablero", <Pendiente titulo="Tablero" endpoint="GET /api/v1/tablero" />),
      },
      {
        path: "/reglas",
        element: protegida("/reglas", <Pendiente titulo="Reglas" endpoint="GET /api/v1/reglas y /api/v1/politica" />),
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
