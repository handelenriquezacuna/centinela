import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { pedir } from "../api/cliente";
import type { EdicionRegla, ListaReglas, Regla } from "../api/tipos";
import { horaCostaRica } from "../formatos";
import { Aviso, type Resultado } from "./Aviso";
import { leerParametros, mensajeDeError, parametrosATexto, sonIguales } from "./errores";

export const CLAVE_REGLAS = ["reglas"] as const;

interface Propiedades {
  regla: Regla;
  puedeEditar: boolean;
}

/**
 * Una regla con su edicion. Cada guardado manda `version_esperada`: si otra persona
 * edito en el medio, la API responde 409, no escribe nada, y la tarjeta se recarga con
 * lo vigente (el formulario se reinicia al cambiar la version).
 */
export function ReglaTarjeta({ regla, puedeEditar }: Propiedades) {
  const clienteConsultas = useQueryClient();
  const [resultado, setResultado] = useState<Resultado>(null);

  async function guardar(cambios: EdicionRegla) {
    try {
      const editada = await pedir<Regla>(`/api/v1/reglas/${regla._id}`, {
        metodo: "PATCH",
        cuerpo: { ...cambios, version_esperada: regla.version ?? undefined },
      });
      clienteConsultas.setQueryData<ListaReglas>(CLAVE_REGLAS, (lista) =>
        lista ? { ...lista, elementos: lista.elementos.map((r) => (r._id === editada._id ? editada : r)) } : lista,
      );
      setResultado({
        tipo: "ok",
        texto: `Regla ${editada.codigo} guardada: ahora está en la versión ${editada.version}.`,
      });
    } catch (fallo) {
      setResultado({ tipo: "error", error: mensajeDeError(fallo, `La regla ${regla.codigo}`) });
      // Lo vigente: si fue un 409, la tarjeta vuelve a mostrar lo que hay en la base.
      await clienteConsultas.invalidateQueries({ queryKey: CLAVE_REGLAS });
    }
  }

  const anteriores = [...regla.versiones_anteriores].reverse();
  return (
    <section id={`regla-${regla._id}`} className={`tarjeta${regla.activa ? "" : " regla-apagada"}`} aria-labelledby={`titulo-${regla._id}`}>
      <div className="regla-cabecera">
        <h3 id={`titulo-${regla._id}`}>
          <span className="chip">{regla.codigo}</span> {regla.descripcion}
        </h3>
        <span className="id-corto">{regla._id}</span>
        <span className="chip">tipo {regla.tipo}</span>
        <span className="chip" data-version={regla.version ?? ""}>
          versión {regla.version}
        </span>
        <span className={`insignia insignia-estado ${regla.activa ? "es-confirmada" : "es-descartada"}`}>
          {regla.activa ? "Activa" : "Apagada"}
        </span>
      </div>
      <p className="subtitulo">
        Umbral <strong>{regla.umbral}</strong> · peso <strong>{regla.peso}</strong> · parámetros{" "}
        <code>{parametrosATexto(regla.parametros)}</code>
      </p>
      <Aviso resultado={resultado} />
      {puedeEditar ? <FormularioRegla key={regla.version} regla={regla} alGuardar={guardar} alInvalido={(texto) => setResultado({ tipo: "error", error: { texto, detalles: [] } })} /> : null}
      <details>
        <summary>Versiones anteriores ({anteriores.length})</summary>
        {anteriores.length > 0 ? (
          <table className="tabla-simple">
            <caption className="visualmente-oculto">Versiones anteriores de {regla.codigo}</caption>
            <thead>
              <tr>
                <th scope="col">Versión</th>
                <th scope="col">Umbral</th>
                <th scope="col">Peso</th>
                <th scope="col">Activa</th>
                <th scope="col">Parámetros</th>
                <th scope="col">Reemplazada (hora CR)</th>
              </tr>
            </thead>
            <tbody>
              {anteriores.map((v) => (
                <tr key={v.version}>
                  <td>{v.version}</td>
                  <td>{v.umbral}</td>
                  <td>{v.peso}</td>
                  <td>{v.activa ? "Sí" : "No"}</td>
                  <td>
                    <code>{parametrosATexto(v.parametros)}</code>
                  </td>
                  <td>{horaCostaRica(v.reemplazada_en)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="subtitulo">Esta regla nunca se ha editado.</p>
        )}
      </details>
    </section>
  );
}

interface PropiedadesFormulario {
  regla: Regla;
  alGuardar: (cambios: EdicionRegla) => Promise<void>;
  alInvalido: (texto: string) => void;
}

function FormularioRegla({ regla, alGuardar, alInvalido }: PropiedadesFormulario) {
  const [umbral, setUmbral] = useState(regla.umbral === null ? "" : String(regla.umbral));
  const [peso, setPeso] = useState(String(regla.peso));
  const [activa, setActiva] = useState(regla.activa ? "true" : "false");
  const [parametros, setParametros] = useState(parametrosATexto(regla.parametros));
  const [enviando, setEnviando] = useState(false);
  const id = regla._id;

  async function enviar(evento: FormEvent) {
    evento.preventDefault();
    const cambios: EdicionRegla = {};
    if (umbral.trim() !== "") {
      const valor = Number(umbral);
      if (!Number.isFinite(valor) || valor < 0) return alInvalido("El umbral tiene que ser un número mayor o igual a 0.");
      if (valor !== regla.umbral) cambios.umbral = valor;
    }
    const pesoNumero = Number(peso);
    if (peso.trim() === "" || !Number.isInteger(pesoNumero) || pesoNumero < 0 || pesoNumero > 100) {
      return alInvalido("El peso tiene que ser un entero de 0 a 100.");
    }
    if (pesoNumero !== regla.peso) cambios.peso = pesoNumero;
    const lectura = leerParametros(parametros);
    if ("error" in lectura) return alInvalido(`Parámetros: ${lectura.error}.`);
    if (!sonIguales(lectura.valor, regla.parametros)) cambios.parametros = lectura.valor;
    if ((activa === "true") !== regla.activa) cambios.activa = activa === "true";

    if (Object.keys(cambios).length === 0) return alInvalido("No hay cambios que guardar.");

    setEnviando(true);
    try {
      await alGuardar(cambios);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form className="form-regla" noValidate onSubmit={(e) => void enviar(e)} aria-label={`Editar ${regla.codigo}`}>
      <div className="campo">
        <label htmlFor={`umbral-${id}`}>Umbral</label>
        <input id={`umbral-${id}`} type="number" step="any" min="0" value={umbral} onChange={(e) => setUmbral(e.target.value)} />
      </div>
      <div className="campo">
        <label htmlFor={`peso-${id}`}>Peso (0-100)</label>
        <input id={`peso-${id}`} type="number" step="1" min="0" max="100" value={peso} onChange={(e) => setPeso(e.target.value)} />
      </div>
      <div className="campo">
        <label htmlFor={`activa-${id}`}>Activa</label>
        <select id={`activa-${id}`} value={activa} onChange={(e) => setActiva(e.target.value)}>
          <option value="true">Sí</option>
          <option value="false">No</option>
        </select>
      </div>
      <div className="campo campo-parametros">
        <label htmlFor={`parametros-${id}`}>Parámetros (JSON)</label>
        <input id={`parametros-${id}`} type="text" value={parametros} onChange={(e) => setParametros(e.target.value)} />
      </div>
      <div className="campo campo-acciones">
        <button type="submit" className="boton boton-primario" disabled={enviando}>
          Guardar
        </button>
      </div>
    </form>
  );
}
