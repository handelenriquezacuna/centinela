import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { ErrorApi, pedir } from "../api/cliente";
import type { ElementoTransaccion, Pagina } from "../api/tipos";
import {
  CAMPOS_FILTRO,
  CANALES,
  ESTADOS_ALERTA,
  OPCIONES_LIMITE,
  filtrosVacios,
  leerFiltros,
  parametrosDeApi,
  validarFiltros,
  type Filtros,
} from "../busqueda";
import { colones, etiqueta, horaCostaRica } from "../formatos";

/** Resumen del explain que la API manda con `depurar=true`. */
interface Plan {
  etapas: string[];
  indices: string[];
  documentos_examinados: number;
  claves_examinadas: number;
  milisegundos: number;
}

interface PaginaBusqueda extends Pagina<ElementoTransaccion> {
  plan: Plan | null;
}

const RUTA_CSV = "/api/v1/transacciones/exportar.csv";
const LIMITE_EXPORTACION = 10_000;

function entero(valor: string | null, porOmision: number): number {
  return valor !== null && /^\d{1,15}$/.test(valor) ? Number(valor) : porOmision;
}

/**
 * Busqueda forense (H-19): filtros combinables sobre GET /api/v1/transacciones.
 *
 * Los filtros viven en la URL (`/busqueda?cuenta=...`): se puede recargar, volver atras y
 * compartir el enlace, y el CSV usa exactamente los mismos. La paginacion la acota la API
 * con `app.pagina_maxima`. Dinero en colones enteros, fechas en hora de Costa Rica.
 */
export function Busqueda() {
  const [parametros, fijarParametros] = useSearchParams();
  const filtros = leerFiltros(parametros);
  const pedido = entero(parametros.get("limite"), 0);
  const limite = OPCIONES_LIMITE.find((opcion) => opcion === pedido) ?? 50;
  const desplazamiento = entero(parametros.get("desplazamiento"), 0);
  const depurar = parametros.get("depurar") === "on";
  const errores = validarFiltros(filtros);

  const [borrador, setBorrador] = useState<Filtros>(filtros);
  const [limiteElegido, setLimiteElegido] = useState(String(limite));
  const [conPlan, setConPlan] = useState(depurar);

  const consultaApi = {
    ...parametrosDeApi(filtros),
    limite: String(limite),
    desplazamiento: String(desplazamiento),
    ...(depurar ? { depurar: "true" } : {}),
  };
  const resultado = useQuery({
    queryKey: ["busqueda", consultaApi],
    queryFn: () => pedir<PaginaBusqueda>("/api/v1/transacciones", { consulta: consultaApi }),
    enabled: errores.length === 0,
  });

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    fijarParametros(parametrosDe(borrador, limiteElegido, conPlan, 0));
  }

  function limpiar() {
    setBorrador(filtrosVacios());
    setLimiteElegido("50");
    setConPlan(false);
    fijarParametros({});
  }

  function enlacePagina(nuevoDesplazamiento: number): string {
    return `/busqueda?${new URLSearchParams(parametrosDe(filtros, String(limite), depurar, nuevoDesplazamiento)).toString()}`;
  }

  const csv = `${RUTA_CSV}?${new URLSearchParams(parametrosDeApi(filtros)).toString()}`;
  const pagina = resultado.data;
  const primero = desplazamiento + 1;
  const ultimo = desplazamiento + (pagina?.elementos.length ?? 0);
  const hayAnterior = desplazamiento > 0;
  const haySiguiente = pagina !== undefined && desplazamiento + pagina.limite < pagina.total;

  return (
    <>
      <div className="titulo-pantalla">
        <div>
          <h1>Búsqueda forense</h1>
          <p className="subtitulo">Filtros combinables sobre el histórico de transacciones. Las fechas son días de Costa Rica.</p>
        </div>
      </div>

      <section className="tarjeta" aria-label="Filtros de búsqueda">
        <h2 className="seccion-titulo">Filtros</h2>
        {errores.length > 0 ? (
          <div className="nota-tecnica" role="alert">
            <strong>La búsqueda tiene parámetros inválidos:</strong>
            <ul>
              {errores.map((e) => (
                <li key={`${e.campo}-${e.mensaje}`}>
                  {e.campo}: {e.mensaje}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {resultado.error ? (
          <div className="nota-tecnica" role="alert">
            <strong>{resultado.error.message}</strong>
            {resultado.error instanceof ErrorApi && resultado.error.detalles.length > 0 ? (
              <ul>
                {resultado.error.detalles.map((d) => (
                  <li key={`${d.campo}-${d.mensaje}`}>
                    {d.campo}: {d.mensaje}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        <form className="barra-filtros" onSubmit={enviar} role="search" aria-label="Búsqueda forense">
          <div className="campo">
            <label htmlFor="b-desde">Fecha desde</label>
            <input type="date" id="b-desde" value={borrador.desde} onChange={(e) => setBorrador({ ...borrador, desde: e.target.value })} />
          </div>
          <div className="campo">
            <label htmlFor="b-hasta">Fecha hasta</label>
            <input type="date" id="b-hasta" value={borrador.hasta} onChange={(e) => setBorrador({ ...borrador, hasta: e.target.value })} />
          </div>
          <div className="campo">
            <label htmlFor="b-monto-min">Monto mínimo (₡)</label>
            <input
              type="number"
              id="b-monto-min"
              step="1"
              min="0"
              value={borrador.monto_min}
              onChange={(e) => setBorrador({ ...borrador, monto_min: e.target.value })}
            />
          </div>
          <div className="campo">
            <label htmlFor="b-monto-max">Monto máximo (₡)</label>
            <input
              type="number"
              id="b-monto-max"
              step="1"
              min="0"
              placeholder="Sin tope"
              value={borrador.monto_max}
              onChange={(e) => setBorrador({ ...borrador, monto_max: e.target.value })}
            />
          </div>
          <div className="campo">
            <label htmlFor="b-cuenta">Cuenta (origen o destino)</label>
            <input
              type="text"
              id="b-cuenta"
              placeholder="CTA-001 o 8712-4455"
              maxLength={32}
              value={borrador.cuenta}
              onChange={(e) => setBorrador({ ...borrador, cuenta: e.target.value })}
            />
          </div>
          <div className="campo">
            <label htmlFor="b-canal">Canal</label>
            <select id="b-canal" value={borrador.canal} onChange={(e) => setBorrador({ ...borrador, canal: e.target.value })}>
              <option value="">Todos</option>
              {CANALES.map((canal) => (
                <option key={canal} value={canal}>
                  {etiqueta(canal)}
                </option>
              ))}
            </select>
          </div>
          <div className="campo">
            <label htmlFor="b-estado-alerta">Estado de la alerta asociada</label>
            <select
              id="b-estado-alerta"
              value={borrador.estado_alerta}
              onChange={(e) => setBorrador({ ...borrador, estado_alerta: e.target.value })}
            >
              <option value="">Cualquiera</option>
              {ESTADOS_ALERTA.map((estado) => (
                <option key={estado} value={estado}>
                  {etiqueta(estado)}
                </option>
              ))}
            </select>
          </div>
          <div className="campo">
            <label htmlFor="b-limite">Resultados por página</label>
            <select id="b-limite" value={limiteElegido} onChange={(e) => setLimiteElegido(e.target.value)}>
              {OPCIONES_LIMITE.map((opcion) => (
                <option key={opcion} value={opcion}>
                  {opcion}
                </option>
              ))}
            </select>
          </div>
          <div className="campo">
            <label htmlFor="b-depurar">
              <input
                type="checkbox"
                id="b-depurar"
                style={{ width: "auto", marginRight: "6px" }}
                checked={conPlan}
                onChange={(e) => setConPlan(e.target.checked)}
              />
              Mostrar plan de ejecución
            </label>
          </div>
          <div className="campo campo-acciones">
            <button type="submit" className="boton boton-primario">
              Buscar
            </button>
            <button type="button" className="boton boton-secundario" onClick={limpiar}>
              Limpiar
            </button>
          </div>
        </form>
      </section>

      {resultado.isPending && errores.length === 0 ? <p role="status">Buscando…</p> : null}

      {pagina ? (
        <section className="tarjeta" aria-label="Resultados de búsqueda">
          <div className="titulo-pantalla" style={{ marginBottom: "var(--espacio-3)" }}>
            <div>
              <strong id="total-resultados">{pagina.total} resultados</strong>
              {pagina.elementos.length > 0 ? (
                <span className="subtitulo">
                  {" "}
                  mostrando {primero} a {ultimo}
                </span>
              ) : null}
            </div>
            {pagina.elementos.length > 0 ? (
              <div className="grupo-botones">
                <a className="boton boton-secundario" href={csv} download>
                  Exportar CSV
                </a>
              </div>
            ) : null}
          </div>
          {pagina.elementos.length > 0 ? (
            <p className="nota-tecnica">
              El CSV aplica estos mismos filtros, con montos enteros y fechas en hora de Costa Rica, hasta{" "}
              {LIMITE_EXPORTACION.toLocaleString("es-CR")} filas.
            </p>
          ) : null}

          {pagina.plan ? (
            <p className="nota-tecnica">
              Plan de ejecución: <code>{pagina.plan.etapas.join(" > ")}</code>
              {pagina.plan.indices.length > 0 ? (
                <>
                  {" "}
                  · índice <code>{pagina.plan.indices.join(", ")}</code>
                </>
              ) : (
                " · sin índice"
              )}{" "}
              · {pagina.plan.documentos_examinados} documentos examinados · {pagina.plan.claves_examinadas} claves ·{" "}
              {pagina.plan.milisegundos} ms
            </p>
          ) : null}

          {pagina.elementos.length > 0 ? (
            <>
              <table className="tabla-responsiva">
                <caption className="visualmente-oculto">Transacciones que cumplen los filtros</caption>
                <thead>
                  <tr>
                    <th scope="col">Transacción</th>
                    <th scope="col">Fecha y hora (CR)</th>
                    <th scope="col">Cuenta origen</th>
                    <th scope="col">Cuenta destino</th>
                    <th scope="col">Monto</th>
                    <th scope="col">Canal</th>
                    <th scope="col">Alerta relacionada</th>
                  </tr>
                </thead>
                <tbody>
                  {pagina.elementos.map((t) => (
                    <tr key={t.transaccion_id} data-transaccion={t.transaccion_id} data-monto-crc={t.monto_crc}>
                      <td data-etiqueta="Transacción" className="id-corto">
                        {t.transaccion_id}
                      </td>
                      <td data-etiqueta="Fecha y hora (CR)" className="hora">
                        {horaCostaRica(t.fecha)}
                      </td>
                      <td data-etiqueta="Cuenta origen" className="id-corto">
                        {t.cuenta_origen}
                      </td>
                      <td data-etiqueta="Cuenta destino" className="id-corto">
                        {t.cuenta_destino}
                      </td>
                      <td data-etiqueta="Monto" className="monto">
                        {colones(t.monto_crc)}
                      </td>
                      <td data-etiqueta="Canal">{etiqueta(t.canal)}</td>
                      <td data-etiqueta="Alerta relacionada">
                        {t.alerta_id ? (
                          <Link to={`/detalle/${t.alerta_id}`}>
                            <span className={`insignia insignia-estado es-${t.alerta_estado ?? ""}`}>
                              {t.alerta_id} · {etiqueta(t.alerta_estado)}
                            </span>
                          </Link>
                        ) : (
                          <span className="insignia insignia-estado">Sin alerta</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <nav className="paginacion" aria-label="Paginación de resultados">
                <span>
                  Mostrando {primero} a {ultimo} de {pagina.total}
                </span>
                <div className="controles">
                  {hayAnterior ? (
                    <Link to={enlacePagina(Math.max(desplazamiento - limite, 0))} aria-label="Página anterior">
                      Anterior
                    </Link>
                  ) : (
                    <a href="#anterior" aria-disabled="true" onClick={(e) => e.preventDefault()}>
                      Anterior
                    </a>
                  )}
                  {haySiguiente ? (
                    <Link to={enlacePagina(desplazamiento + limite)} aria-label="Página siguiente">
                      Siguiente
                    </Link>
                  ) : (
                    <a href="#siguiente" aria-disabled="true" onClick={(e) => e.preventDefault()}>
                      Siguiente
                    </a>
                  )}
                </div>
              </nav>
            </>
          ) : (
            <p className="cola-vacia">No hay transacciones que cumplan los filtros.</p>
          )}
        </section>
      ) : null}
    </>
  );
}

/** Los filtros con valor, mas el limite, el plan y el desplazamiento: la URL de la busqueda. */
function parametrosDe(filtros: Filtros, limite: string, depurar: boolean, desplazamiento: number): Record<string, string> {
  const salida: Record<string, string> = {};
  for (const campo of CAMPOS_FILTRO) {
    const valor = filtros[campo].trim();
    if (valor) salida[campo] = valor;
  }
  salida.limite = limite;
  if (depurar) salida.depurar = "on";
  if (desplazamiento > 0) salida.desplazamiento = String(desplazamiento);
  return salida;
}
