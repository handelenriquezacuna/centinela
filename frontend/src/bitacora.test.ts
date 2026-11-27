import {
  filtrosBitacoraVacios,
  leerFiltrosBitacora,
  parametrosDeApiBitacora,
  rotuloDeAccion,
  validarFiltrosBitacora,
  valorDeInsignia,
} from "./bitacora";

describe("filtros de la bitacora", () => {
  it("lee los filtros de la URL y recorta los espacios", () => {
    const filtros = leerFiltrosBitacora(new URLSearchParams("actor=%20AGT-001&accion=regla_editada&objeto=REG-"));
    expect(filtros).toEqual({ ...filtrosBitacoraVacios(), actor: "AGT-001", accion: "regla_editada", objeto: "REG-" });
  });

  it("los dias de Costa Rica viajan con la zona: inicio y fin del dia, sin vacios", () => {
    const api = parametrosDeApiBitacora({ ...filtrosBitacoraVacios(), desde: "2026-11-20", hasta: "2026-11-21", actor: "AGT-001" });
    expect(api).toEqual({
      desde: "2026-11-20T00:00:00-06:00",
      hasta: "2026-11-21T23:59:59.999999-06:00",
      actor: "AGT-001",
    });
    expect(parametrosDeApiBitacora(filtrosBitacoraVacios())).toEqual({});
  });

  it("rechaza fechas irreales, texto con caracteres raros y rangos invertidos", () => {
    const base = filtrosBitacoraVacios();
    expect(validarFiltrosBitacora({ ...base, desde: "2026-02-30" }).map((e) => e.campo)).toEqual(["desde"]);
    expect(validarFiltrosBitacora({ ...base, actor: "AGT 1", objeto: "ALR-.*" }).map((e) => e.campo)).toEqual(["actor", "objeto"]);
    expect(validarFiltrosBitacora({ ...base, desde: "2026-11-21", hasta: "2026-11-20" })).toEqual([
      { campo: "desde", mensaje: "no puede ser posterior a hasta" },
    ]);
    expect(validarFiltrosBitacora({ ...base, desde: "2026-11-20", actor: "AGT-001", objeto: "6033-9001" })).toEqual([]);
  });
});

describe("tipos de accion", () => {
  it("las acciones de alertas y de envios reusan la paleta de estados", () => {
    expect(valorDeInsignia("alerta_en_revision")).toBe("en_revision");
    expect(valorDeInsignia("alerta_confirmada")).toBe("confirmada");
    expect(valorDeInsignia("notificacion_fallido")).toBe("envio-fallido");
  });

  it("cada familia tiene su insignia y una accion desconocida cae en la neutra", () => {
    expect(valorDeInsignia("regla_editada")).toBe("accion-regla");
    expect(valorDeInsignia("politica_editada")).toBe("accion-regla");
    expect(valorDeInsignia("lista_riesgo_alta")).toBe("accion-lista");
    expect(valorDeInsignia("caso_abierto")).toBe("accion-caso");
    expect(valorDeInsignia("respaldo_verificado")).toBe("accion-respaldo");
    expect(valorDeInsignia("respuesta_cliente")).toBe("accion-cliente");
    expect(valorDeInsignia("algo_nuevo")).toBe("accion-otra");
  });

  it("el rotulo es legible y una accion desconocida se muestra sin guiones bajos", () => {
    expect(rotuloDeAccion("alerta_en_revision")).toBe("Alerta tomada");
    expect(rotuloDeAccion("algo_nuevo")).toBe("algo nuevo");
  });
});
