# Estaticos vendorizados

| Archivo | Que es | Version | Origen |
|---|---|---|---|
| `htmx-ext-sse.js` | Extension SSE de htmx (`hx-ext="sse"`) | `htmx-ext-sse` 2.2.4 | `https://unpkg.com/htmx-ext-sse@2.2.4/dist/sse.js` |

## Por que vendorizado y no por CDN

Dos razones, y la segunda es la que manda:

1. **La extension no viene en el nucleo de htmx.** `htmx.min.js` no trae `hx-ext="sse"`;
   es un archivo aparte que hay que cargar despues del nucleo. Quien lo olvide ve un
   panel que no recibe nada y ningun error claro.
2. **El dia de la defensa no se depende de la red.** Un aula sin internet, un proxy que
   filtra `unpkg.com` o una CDN con un mal minuto convierten el canal en vivo en una
   pantalla vacia. El archivo pesa 9 KB.

## Que hace por su cuenta, y por que conviene saberlo

La extension **agrega su propia reconexion** encima de la que ya trae el navegador:
cuando el `EventSource` se cae, reintenta con espera progresiva
(`retryCount * 500 ms`, duplicando y con tope de 128 vueltas). Es decir, hay dos
mecanismos de reintento apilados: el `retry` que el servidor manda en la apertura del
canal (`canal.reintento_ms` del YAML) y este. Si algun dia una reconexion tarda mas de
lo que dice el YAML, el motivo es este, no un error del servidor.

El navegador reenvia solo la cabecera `Last-Event-ID` con el `id` del ultimo evento
recibido. Ese `id` es el token de reanudacion del change stream: ver
`app/api/canal.py`.

## Como se actualiza

Se baja el archivo de la version nueva, se reemplaza, se actualiza la version de la
tabla de arriba y se corre `python tareas.py pruebas --tipo arquitectura`, que verifica
que el archivo exista, que no este vacio y que sea la extension y no otra cosa.
