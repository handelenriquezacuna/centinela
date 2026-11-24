/*
 * A donde volver despues de iniciar sesion: la misma regla que `destino_seguro` de
 * app/api/rutas/acceso.py, para que `?siguiente=//otro.sitio` no saque a nadie del portal.
 *
 * Solo rutas internas. Los navegadores quitan tabuladores y saltos de linea de una URL,
 * asi que `/<TAB>/otro.sitio` terminaria siendo `//otro.sitio`: se rechaza cualquier
 * caracter de control o espacio, la barra invertida y el doble `/` inicial, y la ruta
 * interpretada no puede traer esquema ni dominio ni ser `/login` o `/salir`.
 * Los casos compartidos con la prueba de Python estan en `destino.casos.json`.
 */

export const DESTINO_POR_OMISION = "/panel";

function tieneControlOEspacio(texto: string): boolean {
  for (const caracter of texto) {
    const codigo = caracter.codePointAt(0) ?? 0;
    if (codigo < 0x21 || codigo === 0x7f) {
      return true;
    }
  }
  return false;
}

export function destinoSeguro(siguiente: string | null | undefined): string {
  if (
    !siguiente ||
    !siguiente.startsWith("/") ||
    tieneControlOEspacio(siguiente) ||
    siguiente.includes("\\") ||
    siguiente.startsWith("//")
  ) {
    return DESTINO_POR_OMISION;
  }
  let interpretada: URL;
  try {
    interpretada = new URL(siguiente, "http://centinela.invalid");
  } catch {
    return DESTINO_POR_OMISION;
  }
  if (
    interpretada.origin !== "http://centinela.invalid" ||
    interpretada.pathname === "/login" ||
    interpretada.pathname === "/salir"
  ) {
    return DESTINO_POR_OMISION;
  }
  return siguiente;
}

/** `/login?siguiente=...` para volver a `ruta` despues de entrar. */
export function rutaDeLogin(ruta: string): string {
  if (ruta === "/" || ruta === "/login" || ruta.startsWith("/login?")) {
    return "/login";
  }
  return `/login?siguiente=${encodeURIComponent(ruta)}`;
}
