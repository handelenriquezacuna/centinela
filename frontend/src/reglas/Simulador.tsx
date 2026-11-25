import { useState, type FormEvent } from "react";

import { pedir } from "../api/cliente";
import type { Evaluacion, Regla } from "../api/tipos";
import { TablaDatos, type Columna } from "../componentes/TablaDatos";
import { VistaVacia } from "../componentes/VistaVacia";
import { AvisoRegion, type ResultadoAviso } from "../componentes/AvisoRegion";
import { FormularioCampo } from "../componentes/FormularioCampo";
import { leerParametros, mensajeDeError, parametrosATexto } from "./errores";
import ejemplo from "./simulacion.ejemplo.json";

/**
 * Simular: evalua UNA transaccion con las reglas guardadas, y opcionalmente con una
 * regla propuesta (reemplaza a la guardada solo en esta evaluacion). No escribe nada.
 * El cuerpo es el de POST /api/v1/reglas/simular: la transaccion y el contexto que
 * H-09 precalcula, como JSON.
 */
export function Simulador({ reglas }: { reglas: Regla[] }) {
  const [cuerpo, setCuerpo] = useState("");
  const [reglaId, setReglaId] = useState("");
  const [umbral, setUmbral] = useState("");
  const [peso, setPeso] = useState("");
  const [parametros, setParametros] = useState("");
  const [evaluacion, setEvaluacion] = useState<Evaluacion | null>(null);
  const [resultado, setResultado] = useState<ResultadoAviso>(null);
  const [enviando, setEnviando] = useState(false);

  function elegirRegla(id: string) {
    setReglaId(id);
    const regla = reglas.find((r) => r._id === id);
    setUmbral(regla?.umbral === null || regla === undefined ? "" : String(regla.umbral));
    setPeso(regla === undefined ? "" : String(regla.peso));
    setParametros(regla === undefined ? "" : parametrosATexto(regla.parametros));
  }

  function invalido(texto: string) {
    setEvaluacion(null);
    setResultado({ tipo: "error", error: { texto, detalles: [] } });
  }

  async function enviar(evento: FormEvent) {
    evento.preventDefault();
    let base: unknown;
    try {
      base = JSON.parse(cuerpo);
    } catch {
      return invalido("La transacción y su contexto no son JSON válido.");
    }
    if (typeof base !== "object" || base === null || Array.isArray(base)) {
      return invalido("La transacción y su contexto tienen que ser un objeto JSON con `transaccion` y `contexto`.");
    }
    const peticion: Record<string, unknown> = { ...base };
    const guardada = reglas.find((r) => r._id === reglaId);
    if (guardada !== undefined) {
      const lectura = leerParametros(parametros);
      if ("error" in lectura) return invalido(`Parámetros: ${lectura.error}.`);
      const umbralNumero = Number(umbral);
      const pesoNumero = Number(peso);
      if (umbral.trim() === "" || !Number.isFinite(umbralNumero) || umbralNumero < 0) return invalido("El umbral propuesto tiene que ser un número mayor o igual a 0.");
      if (peso.trim() === "" || !Number.isInteger(pesoNumero) || pesoNumero < 0 || pesoNumero > 100) return invalido("El peso propuesto tiene que ser un entero de 0 a 100.");
      const sinHistoria: Partial<Regla> = { ...guardada };
      delete sinHistoria.versiones_anteriores;
      peticion.reglas = [{ ...sinHistoria, umbral: umbralNumero, peso: pesoNumero, parametros: lectura.valor }];
    }
    setEnviando(true);
    try {
      setEvaluacion(await pedir<Evaluacion>("/api/v1/reglas/simular", { metodo: "POST", cuerpo: peticion }));
      setResultado(null);
    } catch (fallo) {
      setEvaluacion(null);
      setResultado({ tipo: "error", error: mensajeDeError(fallo, "La política", false) });
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section className="tarjeta" id="simulador" aria-labelledby="titulo-simulador">
      <h2 id="titulo-simulador" className="seccion-titulo">
        Simular sobre una transacción
      </h2>
      <p className="subtitulo">Prueba un umbral antes de aplicarlo. No escribe nada en la base.</p>
      <form noValidate onSubmit={(e) => void enviar(e)} aria-label="Simular una transacción">
        <FormularioCampo etiqueta="Transacción y contexto (JSON)">
          <textarea className="area-json" rows={8} spellCheck={false} value={cuerpo} onChange={(e) => setCuerpo(e.target.value)} />
        </FormularioCampo>
        <p>
          <button type="button" className="boton boton-secundario" onClick={() => setCuerpo(JSON.stringify(ejemplo, null, 2))}>
            Cargar el ejemplo TXN-001
          </button>
        </p>
        <div className="form-regla">
          <FormularioCampo etiqueta="Regla propuesta">
            <select value={reglaId} onChange={(e) => elegirRegla(e.target.value)}>
              <option value="">Ninguna: usar el catálogo guardado</option>
              {reglas.map((r) => (
                <option key={r._id} value={r._id}>
                  {r.codigo} {r.descripcion}
                </option>
              ))}
            </select>
          </FormularioCampo>
          {reglaId !== "" ? (
            <>
              <FormularioCampo etiqueta="Umbral propuesto">
                <input type="number" step="any" min="0" value={umbral} onChange={(e) => setUmbral(e.target.value)} />
              </FormularioCampo>
              <FormularioCampo etiqueta="Peso propuesto">
                <input type="number" step="1" min="0" max="100" value={peso} onChange={(e) => setPeso(e.target.value)} />
              </FormularioCampo>
              <div className="campo-parametros">
                <FormularioCampo etiqueta="Parámetros propuestos (JSON)">
                  <input type="text" value={parametros} onChange={(e) => setParametros(e.target.value)} />
                </FormularioCampo>
              </div>
            </>
          ) : null}
          <div className="campo campo-acciones">
            <button type="submit" className="boton boton-primario" disabled={enviando}>
              Simular
            </button>
          </div>
        </div>
      </form>
      <AvisoRegion resultado={resultado} />
      {evaluacion ? <ResultadoSimulacion evaluacion={evaluacion} /> : null}
    </section>
  );
}

const COLUMNAS_DISPARADAS: Columna<Evaluacion["reglas_disparadas"][number]>[] = [
  { clave: "regla", titulo: "Regla", celda: (d) => <strong>{d.codigo}</strong> },
  { clave: "version", titulo: "Versión", celda: (d) => d.version },
  { clave: "aporte", titulo: "Aporte", celda: (d) => d.peso },
  { clave: "explicacion", titulo: "Explicación", celda: (d) => d.explicacion },
];

export function ResultadoSimulacion({ evaluacion }: { evaluacion: Evaluacion }) {
  return (
    <div id="resultado-simulacion" role="status" aria-live="polite">
      <h3>Resultado para {evaluacion.transaccion_id}</h3>
      <p>
        Puntaje <strong id="simulacion-puntaje">{evaluacion.puntaje}</strong> · severidad{" "}
        <strong id="simulacion-severidad">{evaluacion.severidad === null ? "SIN ALERTA" : evaluacion.severidad.toUpperCase()}</strong> ·{" "}
        {evaluacion.genera_alerta ? "genera alerta" : "no genera alerta"} · política {evaluacion.politica_id} versión {evaluacion.politica_version}
      </p>
      <TablaDatos
        leyenda="Reglas disparadas"
        columnas={COLUMNAS_DISPARADAS}
        filas={evaluacion.reglas_disparadas}
        clave={(d) => d.regla_id}
        estado="listo"
        vacia={<VistaVacia titulo="No se disparó ninguna regla." />}
      />
      {evaluacion.reglas_no_evaluadas.length > 0 ? (
        <>
          <h4>No evaluadas</h4>
          <ul>
            {evaluacion.reglas_no_evaluadas.map((n) => (
              <li key={n.regla_id}>
                {n.codigo}: {n.motivo}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

