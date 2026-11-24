import { colones, horaCostaRica } from "../formatos";
import type { DisposicionSankey, NodoSankey } from "../api/tiposRed";

const MAXIMO_NOMBRE = 28;

function recortar(texto: string): string {
  return texto.length > MAXIMO_NOMBRE ? `${texto.slice(0, MAXIMO_NOMBRE - 1)}…` : texto;
}

function estadoDe(nodo: NodoSankey): string {
  if (nodo.victima) return "Víctima";
  if (nodo.en_lista) return "En lista de riesgo";
  if (nodo.inicio) return "Partida";
  if (nodo.externa) return "Cuenta externa";
  return "Sin marca";
}

interface Propiedades {
  disposicion: DisposicionSankey;
}

/**
 * Dibuja el Sankey que el servidor ya dispuso (GET /api/v1/red-mulas/{cuenta}/sankey): la
 * SPA no calcula posiciones, anchos ni trazados, y los montos son los enteros de la base.
 * El SVG es una imagen con resumen (`role="img"`); el contenido equivalente en texto es la
 * tabla de saltos, que va aparte. Las cuentas en lista de riesgo llevan trama diagonal Y
 * el texto "En lista de riesgo": el color solo no basta.
 */
export function Sankey({ disposicion }: Propiedades) {
  const { ancho, alto, nodos, bandas } = disposicion;
  return (
    <div className="grafo-contenedor">
      <svg
        className="grafico sankey"
        id="sankey-svg"
        width={ancho}
        height={alto}
        viewBox={`0 0 ${ancho} ${alto}`}
        role="img"
        aria-label={disposicion.resumen}
      >
        <defs>
          <pattern id="patron-lista" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="#fee2e2" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="#b91c1c" strokeWidth="3" />
          </pattern>
        </defs>
        {bandas.map((b) => (
          <g key={`${b.salto}-${b.transaccion_id}`} className="sankey-banda-grupo">
            <path className="sankey-banda" d={b.d} data-salto={b.salto} data-monto={b.monto_crc} data-grosor={b.grosor}>
              <title>{`${colones(b.monto_crc)} de ${b.origen} a ${b.destino}, ${horaCostaRica(b.fecha)} (hora CR)`}</title>
            </path>
            <text className="sankey-monto" x={b.tx} y={b.ty}>
              {colones(b.monto_crc)}
            </text>
          </g>
        ))}
        {nodos.map((n) => {
          const clases = ["sankey-nodo", n.victima ? "sankey-nodo-victima" : "", n.en_lista ? "sankey-nodo-lista" : "", n.externa ? "sankey-nodo-externa" : ""];
          return (
            <g
              key={`${n.cuenta}-${n.columna}`}
              className={clases.filter(Boolean).join(" ")}
              data-cuenta={n.cuenta}
              data-columna={n.columna}
              data-en-lista={n.en_lista ? "si" : "no"}
              {...(n.victima ? { "data-victima": "si" } : {})}
            >
              {n.victima ? <title>{`Víctima: ${n.nombre ?? ""}`}</title> : null}
              <rect x={n.x} y={n.y} width={n.ancho} height={n.alto} rx={3} {...(n.en_lista ? { fill: "url(#patron-lista)" } : {})} />
              {n.victima ? (
                <text className="sankey-nombre" x={n.x} y={n.y - 36}>
                  {recortar(n.nombre ?? "")}
                </text>
              ) : null}
              <text className="sankey-cuenta" x={n.x} y={n.y - 22}>
                {n.cuenta}
              </text>
              <text className="sankey-estado" x={n.x} y={n.y - 8}>
                {estadoDe(n)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** La alternativa en texto del Sankey: un salto por fila, con colones y hora de Costa Rica. */
export function TablaSaltos({ disposicion }: Propiedades) {
  return (
    <details className="sankey-tabla">
      <summary>Ver los saltos del recorrido en una tabla</summary>
      <table className="tabla-simple" id="tabla-saltos-sankey">
        <caption className="visualmente-oculto">Saltos del recorrido del dinero</caption>
        <thead>
          <tr>
            <th scope="col">Salto</th>
            <th scope="col">Origen</th>
            <th scope="col">Destino</th>
            <th scope="col">Monto</th>
            <th scope="col">Fecha (hora CR)</th>
          </tr>
        </thead>
        <tbody>
          {disposicion.bandas.map((b) => (
            <tr key={`${b.salto}-${b.transaccion_id}`}>
              <td>{b.salto}</td>
              <td className="id-corto">{b.origen}</td>
              <td className="id-corto">{b.destino}</td>
              <td>{colones(b.monto_crc)}</td>
              <td>{horaCostaRica(b.fecha)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
