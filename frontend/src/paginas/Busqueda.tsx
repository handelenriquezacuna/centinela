import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { pedir } from "../api/cliente";
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
import { Cargando } from "../componentes/Cargando";
import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { FechaCR } from "../componentes/FechaCR";
import { FiltroBarra } from "../componentes/FiltroBarra";
import { FormularioCampo } from "../componentes/FormularioCampo";
import { Insignia } from "../componentes/Insignia";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { MontoColones } from "../componentes/MontoColones";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { VistaError } from "../componentes/VistaError";
import { VistaVacia } from "../componentes/VistaVacia";
import { etiqueta } from "../formatos";
import { mensajeDeError } from "../reglas/errores";

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

  function buscar() {
    fijarParametros(parametrosDe(borrador, limiteElegido, conPlan, 0));
  }

  function limpiar() {
    setBorrador(filtrosVacios());
    setLimiteElegido("50");
    setConPlan(false);
    fijarParametros({});
  }

  const csv = `${RUTA_CSV}?${new URLSearchParams(parametrosDeApi(filtros)).toString()}`;
  const pagina = resultado.data;
  const primero = desplazamiento + 1;
  const ultimo = desplazamiento + (pagina?.elementos.length ?? 0);
  const errorApi = resultado.error
    ? mensajeDeError(resultado.error, "La búsqueda")
    : null;

  return (
    <MarcoPagina id="busqueda" ancho="ancho">
      <EncabezadoPagina
        id="busqueda"
        titulo="Búsqueda forense"
        descripcion="Filtros combinables sobre el histórico de transacciones. Las fechas son días de Costa Rica."
      />

      <section className="tarjeta" aria-label="Filtros de búsqueda">
        <h2 className="seccion-titulo">Filtros</h2>
        {errores.length > 0 ? (
          <VistaError
            error={{
              texto: "La búsqueda tiene parámetros inválidos:",
              detalles: errores.map((e) => `${e.campo}: ${e.mensaje}`),
            }}
          />
        ) : null}
        <FiltroBarra rotulo="Búsqueda forense" alBuscar={buscar} alLimpiar={limpiar}>
          <FormularioCampo etiqueta="Fecha desde">
            <input type="date" value={borrador.desde} onChange={(e) => setBorrador({ ...borrador, desde: e.target.value })} />
          </FormularioCampo>
          <FormularioCampo etiqueta="Fecha hasta">
            <input type="date" value={borrador.hasta} onChange={(e) => setBorrador({ ...borrador, hasta: e.target.value })} />
          </FormularioCampo>
          <FormularioCampo etiqueta="Monto mínimo (₡)">
            <input type="number" step="1" min="0" value={borrador.monto_min} onChange={(e) => setBorrador({ ...borrador, monto_min: e.target.value })} />
          </FormularioCampo>
          <FormularioCampo etiqueta="Monto máximo (₡)">
            <input
              type="number"
              step="1"
              min="0"
              placeholder="Sin tope"
              value={borrador.monto_max}
              onChange={(e) => setBorrador({ ...borrador, monto_max: e.target.value })}
            />
          </FormularioCampo>
          <FormularioCampo etiqueta="Cuenta (origen o destino)">
            <input
              type="text"
              placeholder="CTA-001 o 8712-4455"
              maxLength={32}
              value={borrador.cuenta}
              onChange={(e) => setBorrador({ ...borrador, cuenta: e.target.value })}
            />
          </FormularioCampo>
          <FormularioCampo etiqueta="Canal">
            <select value={borrador.canal} onChange={(e) => setBorrador({ ...borrador, canal: e.target.value })}>
              <option value="">Todos</option>
              {CANALES.map((canal) => (
                <option key={canal} value={canal}>
                  {etiqueta(canal)}
                </option>
              ))}
            </select>
          </FormularioCampo>
          <FormularioCampo etiqueta="Estado de la alerta asociada">
            <select value={borrador.estado_alerta} onChange={(e) => setBorrador({ ...borrador, estado_alerta: e.target.value })}>
              <option value="">Cualquiera</option>
              {ESTADOS_ALERTA.map((estado) => (
                <option key={estado} value={estado}>
                  {etiqueta(estado)}
                </option>
              ))}
            </select>
          </FormularioCampo>
          <FormularioCampo etiqueta="Resultados por página">
            <select value={limiteElegido} onChange={(e) => setLimiteElegido(e.target.value)}>
              {OPCIONES_LIMITE.map((opcion) => (
                <option key={opcion} value={opcion}>
                  {opcion}
                </option>
              ))}
            </select>
          </FormularioCampo>
          <div className="campo campo-casilla">
            <label htmlFor="b-depurar">
              <input type="checkbox" id="b-depurar" checked={conPlan} onChange={(e) => setConPlan(e.target.checked)} />
              Mostrar plan de ejecución
            </label>
          </div>
        </FiltroBarra>
      </section>

      {resultado.isPending && errores.length === 0 ? <Cargando texto="Buscando…" /> : null}
      {errorApi ? <VistaError error={errorApi} alReintentar={() => void resultado.refetch()} /> : null}

      {pagina ? (
        <section className="tarjeta" aria-label="Resultados de búsqueda">
          <div className="titulo-pantalla resultados-cabecera">
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

          <TablaDatos
            leyenda="Transacciones que cumplen los filtros"
            columnas={COLUMNAS}
            filas={pagina.elementos}
            clave={(t) => t.transaccion_id}
            estado="listo"
            atributosFila={(t) => ({ "data-transaccion": t.transaccion_id, "data-monto-crc": t.monto_crc })}
            paginacion={{ desplazamiento, limite: pagina.limite, total: pagina.total }}
            alCambiarPagina={(nuevo) => fijarParametros(parametrosDe(filtros, String(limite), depurar, nuevo))}
            vacia={<VistaVacia titulo="No hay transacciones que cumplan los filtros." texto="Pruebe con menos filtros o con otro rango de fechas." />}
          />
        </section>
      ) : null}
    </MarcoPagina>
  );
}

const COLUMNAS: Columna<ElementoTransaccion>[] = [
  { clave: "transaccion", titulo: "Transacción", celda: (t) => t.transaccion_id, clase: "id-corto" },
  { clave: "fecha", titulo: "Fecha y hora (CR)", celda: (t) => <FechaCR valor={t.fecha} /> },
  { clave: "origen", titulo: "Cuenta origen", celda: (t) => t.cuenta_origen, clase: "id-corto" },
  { clave: "destino", titulo: "Cuenta destino", celda: (t) => t.cuenta_destino, clase: "id-corto" },
  { clave: "monto", titulo: "Monto", celda: (t) => <MontoColones valor={t.monto_crc} /> },
  { clave: "canal", titulo: "Canal", celda: (t) => etiqueta(t.canal) },
  {
    clave: "alerta",
    titulo: "Alerta relacionada",
    celda: (t) =>
      t.alerta_id ? (
        <Link to={`/detalle/${t.alerta_id}`}>
          <Insignia tipo="estado" valor={t.alerta_estado ?? ""} texto={`${t.alerta_id} · ${etiqueta(t.alerta_estado)}`} />
        </Link>
      ) : (
        <span className="insignia insignia-estado">Sin alerta</span>
      ),
  },
];

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
