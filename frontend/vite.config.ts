/*
 * Build y servidor de desarrollo de la SPA (T-61).
 *
 * - `npm run build` escribe en `app/estaticos/spa/` (ignorada por git). FastAPI la sirve
 *   desde el montaje `/estaticos` de siempre, por eso `base` es `/estaticos/spa/` en el
 *   build: el index pide `/estaticos/spa/assets/...` y las rutas de pagina devuelven ese
 *   index (app/api/rutas/spa.py).
 * - `npm run dev` levanta Vite en 127.0.0.1:8173 con recarga en caliente y manda al
 *   backend (`CENTINELA_API`, por omision http://127.0.0.1:8000) todo lo que no es la SPA:
 *   la API, el canal SSE, /estado, /salud y los estaticos compartidos (centinela.css).
 *   Mismo origen para el navegador, asi que la cookie de sesion viaja sola.
 */
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const backend = process.env.CENTINELA_API ?? "http://127.0.0.1:8000";

export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === "build" ? "/estaticos/spa/" : "/",
  build: {
    outDir: "../app/estaticos/spa",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    host: "127.0.0.1",
    port: 8173,
    strictPort: true,
    proxy: {
      "/api": { target: backend },
      "/estado": { target: backend },
      "/salud": { target: backend },
      "/estaticos": { target: backend },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/pruebas/preparar.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
}));
