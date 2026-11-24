/*
 * Graficos SVG propios (sin libreria): el servidor entrega la geometria ya calculada
 * (`graficos` de GET /api/v1/tablero) y esto solo la dibuja. Cada grafico tiene su
 * alternativa textual en `aria-label` con los mismos valores.
 */
import type { GraficoBarras, GraficoColumnas } from "../api/tipos";
import { etiqueta } from "../formatos";

function rotuloLegible(rotulo: string): string {
  return etiqueta(rotulo);
}

export function BarrasHorizontales({ grafico, titulo }: { grafico: GraficoBarras; titulo: string }) {
  const alternativa = `${titulo}: ${grafico.filas.map((f) => `${rotuloLegible(f.rotulo)} ${f.valor}`).join(", ")}`;
  return (
    <svg className="grafico" viewBox={`0 0 ${grafico.ancho} ${grafico.alto}`} role="img" aria-label={alternativa}>
      <title>{titulo}</title>
      {grafico.filas.map((fila) => (
        <g key={fila.rotulo}>
          <text x={0} y={fila.y + 17}>
            {rotuloLegible(fila.rotulo)}
          </text>
          <rect className={fila.clase} x={grafico.etiqueta} y={fila.y + 4} width={fila.ancho} height={16} rx={2} />
          <text className="valor" x={grafico.etiqueta + fila.ancho + 6} y={fila.y + 17}>
            {fila.valor}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function ColumnasPorHora({ grafico, titulo }: { grafico: GraficoColumnas; titulo: string }) {
  const conDatos = grafico.columnas.filter((c) => c.valor > 0);
  const alternativa = `${titulo}: ${conDatos.length === 0 ? "sin alertas" : conDatos.map((c) => `${c.hora}:00 ${c.valor}`).join(", ")}`;
  return (
    <svg className="grafico" viewBox={`0 0 ${grafico.ancho} ${grafico.alto}`} role="img" aria-label={alternativa}>
      <title>{titulo}</title>
      {grafico.columnas.map((columna) => (
        <g key={columna.hora}>
          <rect className="barra-hora" x={columna.x + 2} y={columna.y} width={grafico.paso - 4} height={columna.alto}>
            <title>{`${columna.hora}:00 - ${columna.valor}`}</title>
          </rect>
          {Number(columna.hora) % 3 === 0 ? (
            <text x={columna.x} y={grafico.base + 14}>
              {columna.hora}
            </text>
          ) : null}
        </g>
      ))}
    </svg>
  );
}
