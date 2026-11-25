import { EncabezadoPagina } from "../componentes/EncabezadoPagina";
import { MarcoPagina } from "../componentes/MarcoPagina";
import { VistaVacia } from "../componentes/VistaVacia";

/** Marcador: el modulo de respaldos lo construye otra rama; la entrada del menu ya existe. */
export function Respaldos() {
  return (
    <MarcoPagina id="respaldos">
      <EncabezadoPagina id="respaldos" titulo="Respaldos" descripcion="Copias de la base de datos del portal." />
      <section className="tarjeta">
        <VistaVacia titulo="Disponible cuando entre el módulo de respaldos" />
      </section>
    </MarcoPagina>
  );
}
