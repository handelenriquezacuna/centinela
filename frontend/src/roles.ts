/*
 * Que rol hace falta para una pantalla, solo para decirlo en el mensaje de "acceso
 * denegado". Quien decide es `app/seguridad/permisos.py`: si esta lista se queda atras, lo
 * unico que pasa es que el mensaje es menos preciso, nunca que se abra una pantalla.
 */
const REQUIERE: [prefijo: string, quien: string][] = [
  ["/administracion", "el administrador"],
  ["/tablero", "el supervisor y el administrador"],
  ["/reglas", "el supervisor y el administrador"],
];

/** Quien puede abrir la pantalla de esa plantilla de ruta, en palabras; `null` si no se sabe. */
export function quienPuedeAbrir(plantilla: string): string | null {
  return REQUIERE.find(([prefijo]) => plantilla === prefijo || plantilla.startsWith(`${prefijo}/`))?.[1] ?? null;
}
