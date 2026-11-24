import { colones, decimal } from "../formatos";
import type { DisposicionGrafo } from "../api/tiposRed";

interface Propiedades {
  grafo: DisposicionGrafo;
  cuenta: string;
}

/** El grafo de la red de mulas, tal como lo dispuso el servidor (posiciones en px enteros). */
export function GrafoRed({ grafo, cuenta }: Propiedades) {
  return (
    <svg
      className="grafico grafo"
      id="grafo-red"
      width={grafo.ancho}
      height={grafo.alto}
      viewBox={`0 0 ${grafo.ancho} ${grafo.alto}`}
      role="img"
      aria-label={`Red de mulas de ${cuenta}: ${grafo.nodos.length} cuentas y ${grafo.aristas.length} transferencias`}
    >
      <defs>
        <marker id="flecha" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="#475569" />
        </marker>
      </defs>
      {grafo.aristas.map((a) => (
        <g key={`${a.salto}-${a.x1}-${a.y1}-${a.x2}-${a.y2}`}>
          <line className="arista" x1={a.x1} y1={a.y1} x2={a.x2} y2={a.y2} markerEnd="url(#flecha)" />
          <text className="etiqueta-arista" x={a.tx} y={a.ty}>
            {colones(a.monto_crc)}
            {a.minutos !== null ? ` · ${decimal(a.minutos)} min` : ""}
          </text>
        </g>
      ))}
      {grafo.nodos.map((n) => {
        const clases = ["nodo", n.inicio ? "nodo-inicio" : "", n.externa ? "nodo-externa" : "", n.en_lista ? "nodo-lista" : ""];
        return (
          <g className="nodo-grupo" key={n.cuenta} data-cuenta={n.cuenta} data-en-lista={n.en_lista ? "si" : "no"}>
            <rect className={clases.filter(Boolean).join(" ")} x={n.x} y={n.y} width={n.ancho} height={n.alto} rx={6} />
            <text className="cuenta" x={n.x + Math.floor(n.ancho / 2)} y={n.y + 19}>
              {n.cuenta}
            </text>
            <text className="sub" x={n.x + Math.floor(n.ancho / 2)} y={n.y + 35}>
              {n.inicio ? "partida" : n.externa ? "externa" : n.cuenta_id}
              {n.en_lista ? " · en lista de riesgo" : ""}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
