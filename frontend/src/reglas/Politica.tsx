import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { pedir } from "../api/cliente";
import type { CorteSeveridad, EdicionPolitica, PoliticaVigente } from "../api/tipos";
import { FechaCR } from "../componentes/FechaCR";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { VistaVacia } from "../componentes/VistaVacia";
import { etiqueta } from "../formatos";
import { AvisoRegion, type ResultadoAviso } from "../componentes/AvisoRegion";
import { FormularioCampo } from "../componentes/FormularioCampo";
import { mensajeDeError } from "./errores";

export const CLAVE_POLITICA = ["politica"] as const;

const COLUMNAS_CORTES: Columna<CorteSeveridad>[] = [
  { clave: "severidad", titulo: "Severidad", celda: (c) => etiqueta(c.severidad) },
  { clave: "desde", titulo: "Desde", celda: (c) => c.puntaje_minimo },
  { clave: "hasta", titulo: "Hasta", celda: (c) => c.puntaje_maximo },
];

const COLUMNAS_VERSIONES: Columna<PoliticaVigente["versiones_anteriores"][number]>[] = [
  { clave: "version", titulo: "Versión", celda: (v) => v.version },
  { clave: "umbral", titulo: "Umbral", celda: (v) => v.umbral_alerta },
  { clave: "maximo", titulo: "Máximo", celda: (v) => v.puntaje_maximo },
  { clave: "cortes", titulo: "Cortes", celda: (v) => v.cortes_severidad.map((c) => `${etiqueta(c.severidad)} ${c.puntaje_minimo}-${c.puntaje_maximo}`).join("; ") },
  { clave: "reemplazada", titulo: "Reemplazada (hora CR)", celda: (v) => <FechaCR valor={v.reemplazada_en} /> },
];

interface Propiedades {
  politica: PoliticaVigente;
  puedeEditar: boolean;
}

/**
 * Politica de deteccion: umbral de alerta, puntaje maximo y cortes de severidad.
 * Misma concurrencia optimista que una regla (`version_esperada`). Rige en la
 * siguiente evaluacion del detector.
 */
export function Politica({ politica, puedeEditar }: Propiedades) {
  const clienteConsultas = useQueryClient();
  const [resultado, setResultado] = useState<ResultadoAviso>(null);

  async function guardar(cambios: EdicionPolitica) {
    try {
      const editada = await pedir<PoliticaVigente>("/api/v1/politica", {
        metodo: "PATCH",
        cuerpo: { ...cambios, version_esperada: politica.version },
      });
      clienteConsultas.setQueryData(CLAVE_POLITICA, editada);
      setResultado({ tipo: "ok", texto: `Política guardada: ahora está en la versión ${editada.version}. Rige desde la próxima evaluación.` });
    } catch (fallo) {
      setResultado({ tipo: "error", error: mensajeDeError(fallo, "La política") });
      await clienteConsultas.invalidateQueries({ queryKey: CLAVE_POLITICA });
    }
  }

  const anteriores = [...politica.versiones_anteriores].reverse();
  return (
    <section className="tarjeta" id="politica" aria-labelledby="titulo-politica">
      <h2 id="titulo-politica" className="seccion-titulo">
        Política vigente
      </h2>
      <p>
        Umbral de alerta <strong id="politica-umbral">{politica.umbral_alerta}</strong> · puntaje máximo{" "}
        <strong>{politica.puntaje_maximo}</strong> · <span id="politica-version">versión {politica.version}</span>
      </p>
      <AvisoRegion resultado={resultado} />
      {puedeEditar ? (
        <FormularioPolitica
          key={politica.version}
          politica={politica}
          alGuardar={guardar}
          alInvalido={(texto) => setResultado({ tipo: "error", error: { texto, detalles: [] } })}
        />
      ) : (
        <TablaDatos leyenda="Cortes de severidad" columnas={COLUMNAS_CORTES} filas={politica.cortes_severidad} clave={(c) => c.severidad} estado="listo" />
      )}
      <details>
        <summary>Versiones anteriores de la política ({anteriores.length})</summary>
        {anteriores.length > 0 ? (
          <TablaDatos leyenda="Versiones anteriores de la política" columnas={COLUMNAS_VERSIONES} filas={anteriores} clave={(v) => String(v.version)} estado="listo" />
        ) : (
          <VistaVacia titulo="La política nunca se ha editado." />
        )}
      </details>
    </section>
  );
}

interface PropiedadesFormulario {
  politica: PoliticaVigente;
  alGuardar: (cambios: EdicionPolitica) => Promise<void>;
  alInvalido: (texto: string) => void;
}

function FormularioPolitica({ politica, alGuardar, alInvalido }: PropiedadesFormulario) {
  const [umbral, setUmbral] = useState(String(politica.umbral_alerta));
  const [maximo, setMaximo] = useState(String(politica.puntaje_maximo));
  const [cortes, setCortes] = useState(politica.cortes_severidad.map((c) => ({ ...c, minimo: String(c.puntaje_minimo), maximo: String(c.puntaje_maximo) })));
  const [enviando, setEnviando] = useState(false);

  function cambiarCorte(indice: number, campo: "minimo" | "maximo", valor: string) {
    setCortes((previos) => previos.map((c, i) => (i === indice ? { ...c, [campo]: valor } : c)));
  }

  async function enviar(evento: FormEvent) {
    evento.preventDefault();
    const entero = (texto: string) => (texto.trim() !== "" && Number.isInteger(Number(texto)) && Number(texto) >= 0 ? Number(texto) : null);
    const umbralNumero = entero(umbral);
    const maximoNumero = entero(maximo);
    if (umbralNumero === null) return alInvalido("El umbral de alerta tiene que ser un entero mayor o igual a 0.");
    if (maximoNumero === null || maximoNumero < 1) return alInvalido("El puntaje máximo tiene que ser un entero mayor o igual a 1.");
    const nuevos: CorteSeveridad[] = [];
    for (const corte of cortes) {
      const minimo = entero(corte.minimo);
      const tope = entero(corte.maximo);
      if (minimo === null || tope === null) return alInvalido(`Los límites del corte ${etiqueta(corte.severidad)} tienen que ser enteros.`);
      nuevos.push({ severidad: corte.severidad, puntaje_minimo: minimo, puntaje_maximo: tope });
    }

    const cambios: EdicionPolitica = {};
    if (umbralNumero !== politica.umbral_alerta) cambios.umbral_alerta = umbralNumero;
    if (maximoNumero !== politica.puntaje_maximo) cambios.puntaje_maximo = maximoNumero;
    if (JSON.stringify(nuevos) !== JSON.stringify(politica.cortes_severidad.map(({ severidad, puntaje_minimo, puntaje_maximo }) => ({ severidad, puntaje_minimo, puntaje_maximo })))) {
      cambios.cortes_severidad = nuevos;
    }
    if (Object.keys(cambios).length === 0) return alInvalido("No hay cambios que guardar.");

    setEnviando(true);
    try {
      await alGuardar(cambios);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form className="form-regla form-politica" noValidate onSubmit={(e) => void enviar(e)} aria-label="Editar la política">
      <FormularioCampo etiqueta="Umbral de alerta" ayuda="Desde este puntaje se crea una alerta.">
        <input type="number" step="1" min="0" value={umbral} onChange={(e) => setUmbral(e.target.value)} />
      </FormularioCampo>
      <FormularioCampo etiqueta="Puntaje máximo" ayuda="Tope del puntaje de una alerta.">
        <input type="number" step="1" min="1" value={maximo} onChange={(e) => setMaximo(e.target.value)} />
      </FormularioCampo>
      <fieldset className="cortes-severidad">
        <legend>Cortes de severidad (límites inclusivos)</legend>
        {cortes.map((corte, indice) => (
          <div key={corte.severidad} className="corte-fila">
            <strong className="corte-nombre">{etiqueta(corte.severidad)}</strong>
            <FormularioCampo etiqueta={`Desde (${etiqueta(corte.severidad)})`}>
              <input type="number" step="1" min="0" value={corte.minimo} onChange={(e) => cambiarCorte(indice, "minimo", e.target.value)} />
            </FormularioCampo>
            <FormularioCampo etiqueta={`Hasta (${etiqueta(corte.severidad)})`}>
              <input type="number" step="1" min="0" value={corte.maximo} onChange={(e) => cambiarCorte(indice, "maximo", e.target.value)} />
            </FormularioCampo>
          </div>
        ))}
      </fieldset>
      <div className="campo campo-acciones">
        <button type="submit" className="boton boton-primario" disabled={enviando}>
          Guardar política
        </button>
      </div>
    </form>
  );
}
