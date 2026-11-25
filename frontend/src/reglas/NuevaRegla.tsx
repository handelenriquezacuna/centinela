import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { pedir } from "../api/cliente";
import type { ListaReglas, NuevaRegla as CuerpoNuevaRegla, Regla, TipoSoportado } from "../api/tipos";
import { AvisoRegion, type ResultadoAviso } from "../componentes/AvisoRegion";
import { FormularioCampo } from "../componentes/FormularioCampo";
import { leerParametros, mensajeDeError, parametrosATexto } from "./errores";
import { CLAVE_REGLAS } from "./ReglaTarjeta";

function parametrosIniciales(tipo: TipoSoportado | undefined): string {
  return parametrosATexto(Object.fromEntries((tipo?.parametros ?? []).map((p) => [p.nombre, 0])));
}

/** Alta de una regla de un tipo que el motor ya sabe evaluar. El servidor asigna el REG-NNN. */
export function NuevaRegla({ tipos }: { tipos: TipoSoportado[] }) {
  const clienteConsultas = useQueryClient();
  const evaluables = tipos.filter((t) => t.evaluable);
  const [codigo, setCodigo] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [tipoElegido, setTipoElegido] = useState(evaluables[0]?.tipo ?? "");
  const [umbral, setUmbral] = useState("0");
  const [peso, setPeso] = useState("10");
  const [activa, setActiva] = useState(true);
  const [parametros, setParametros] = useState(parametrosIniciales(evaluables[0]));
  const [resultado, setResultado] = useState<ResultadoAviso>(null);
  const [enviando, setEnviando] = useState(false);
  const tipo = tipos.find((t) => t.tipo === tipoElegido);

  function elegirTipo(nombre: string) {
    setTipoElegido(nombre);
    setParametros(parametrosIniciales(tipos.find((t) => t.tipo === nombre)));
  }

  function invalido(texto: string) {
    setResultado({ tipo: "error", error: { texto, detalles: [] } });
  }

  async function enviar(evento: FormEvent) {
    evento.preventDefault();
    if (!/^R-\d{2}$/.test(codigo)) return invalido("El código tiene la forma R-13 (R, guion y dos dígitos).");
    if (descripcion.trim() === "") return invalido("La descripción es obligatoria.");
    if (tipoElegido === "") return invalido("Elija un tipo de regla.");
    const umbralNumero = Number(umbral);
    if (umbral.trim() === "" || !Number.isFinite(umbralNumero) || umbralNumero < 0) return invalido("El umbral tiene que ser un número mayor o igual a 0.");
    const pesoNumero = Number(peso);
    if (peso.trim() === "" || !Number.isInteger(pesoNumero) || pesoNumero < 0 || pesoNumero > 100) return invalido("El peso tiene que ser un entero de 0 a 100.");
    const lectura = leerParametros(parametros);
    if ("error" in lectura) return invalido(`Parámetros: ${lectura.error}.`);

    const cuerpo: CuerpoNuevaRegla = {
      codigo,
      descripcion: descripcion.trim(),
      tipo: tipoElegido,
      umbral: umbralNumero,
      parametros: lectura.valor,
      peso: pesoNumero,
      activa,
    };
    setEnviando(true);
    try {
      const creada = await pedir<Regla>("/api/v1/reglas", { metodo: "POST", cuerpo });
      clienteConsultas.setQueryData<ListaReglas>(CLAVE_REGLAS, (lista) =>
        lista ? { total: lista.total + 1, elementos: [...lista.elementos, creada].sort((a, b) => a.codigo.localeCompare(b.codigo)) } : lista,
      );
      setResultado({ tipo: "ok", texto: `Regla ${creada.codigo} creada como ${creada._id}, en la versión 1.` });
      setCodigo("");
      setDescripcion("");
    } catch (fallo) {
      setResultado({ tipo: "error", error: mensajeDeError(fallo, "El catálogo", false) });
    } finally {
      setEnviando(false);
    }
  }

  return (
    <section className="tarjeta" id="nueva-regla" aria-labelledby="titulo-nueva-regla">
      <h2 id="titulo-nueva-regla" className="seccion-titulo">
        Crear una regla
      </h2>
      <p className="subtitulo">Solo de un tipo que el motor ya sabe evaluar. El servidor asigna el identificador y la deja en la versión 1.</p>
      <AvisoRegion resultado={resultado} />
      <form className="form-regla" noValidate onSubmit={(e) => void enviar(e)} aria-label="Crear una regla">
        <FormularioCampo etiqueta="Código">
          <input type="text" placeholder="R-13" value={codigo} onChange={(e) => setCodigo(e.target.value)} />
        </FormularioCampo>
        <div className="campo-parametros">
          <FormularioCampo etiqueta="Descripción">
            <input type="text" value={descripcion} onChange={(e) => setDescripcion(e.target.value)} />
          </FormularioCampo>
        </div>
        <FormularioCampo etiqueta="Tipo">
          <select value={tipoElegido} onChange={(e) => elegirTipo(e.target.value)}>
            {evaluables.map((t) => (
              <option key={t.tipo} value={t.tipo}>
                {t.tipo}
              </option>
            ))}
          </select>
        </FormularioCampo>
        <FormularioCampo etiqueta="Umbral">
          <input type="number" step="any" min="0" value={umbral} onChange={(e) => setUmbral(e.target.value)} />
        </FormularioCampo>
        <FormularioCampo etiqueta="Peso (0-100)">
          <input type="number" step="1" min="0" max="100" value={peso} onChange={(e) => setPeso(e.target.value)} />
        </FormularioCampo>
        <div className="campo-parametros">
          <FormularioCampo
            etiqueta="Parámetros (JSON)"
            ayuda={
              tipo
                ? `Umbral: ${tipo.umbral}. Parámetros que lee: ${tipo.parametros.length > 0 ? tipo.parametros.map((p) => `${p.nombre} (${p.descripcion})`).join("; ") : "ninguno"}.`
                : undefined
            }
          >
            <input type="text" value={parametros} onChange={(e) => setParametros(e.target.value)} />
          </FormularioCampo>
        </div>
        <div className="campo campo-casilla">
          <label htmlFor="nueva-activa">
            <input id="nueva-activa" type="checkbox" checked={activa} onChange={(e) => setActiva(e.target.checked)} />
            Activa
          </label>
        </div>
        <div className="campo campo-acciones">
          <button type="submit" className="boton boton-primario" disabled={enviando}>
            Crear regla
          </button>
        </div>
      </form>
    </section>
  );
}
