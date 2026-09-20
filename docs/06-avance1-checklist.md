# 06 · Checklist del Avance 1

Documento **IEEE** de 12 apartados (Alternativa A). **Vence lunes 26 de octubre.**
No omitir ni renombrar ningún apartado — si un título cambia, ese apartado no
se califica. Se redacta a mano: usar IA aquí es causal de nulidad.

> Meta interna: **borrador completo el 21-22 de octubre**, para dejar 3-4 días
> de revisión antes de subirlo. No al filo del 26.

## 0 · Antes de escribir una sola línea

- [ ] Bajar la **plantilla IEEE oficial** del aula (pestaña "Introducción" →
      carpeta de plantillas). No usar una plantilla genérica de internet.
- [ ] Confirmar la nomenclatura real del archivo — el PDF dice
      `GX_DSC101_Avance1_LN`, pero `DSC101` no es el código de este curso
      (`SC609`) y `LN` no está explicado. Preguntar en la clase del 21 sep.
- [ ] Confirmar el número de grupo (`GX`) asignado por el profesor.
- [ ] Recordar: **cada uno de los 4 sube el mismo documento** con su nombre en
      la nomenclatura — no lo sube solo una persona por el grupo.

## 1 · Los 4 apartados que ya están escritos (solo hay que adaptar el tono)

Vienen de `01-vision-y-alcance.md` y `04-arquitectura.md`. Pasarlos a la
plantilla IEEE, en tono formal de tercera persona, sin las tablas markdown
(convertir a tablas de Word o prosa).

- [ ] **2. Definición del problema** — de la sección "1. El problema" (vishing,
      por qué el banco no puede blindarse con identidad).
- [ ] **8. Solución planteada** — de "2. Qué construimos" (Centinela en una frase).
- [ ] **11. Tecnologías que se utilizarán** — de la plantilla de grupo ya llena
      (FastAPI, Motor, React, Tailwind, Docker, Faker, Mailtrap, GitHub).
- [ ] **12. Arquitectura preliminar** — de `04-arquitectura.md`. **Exportar los
      diagramas Mermaid a imagen** (captura de pantalla renderizada en GitHub, o
      recrearlos en draw.io/Excalidraw) — Word no renderiza Mermaid. Usar el
      diagrama de Contenedores (sección 3) y el ER de decisiones de modelado
      (sección 5) como mínimo — ya cubren el rubro 2 (documentación con diagramas).

## 2 · Existe una base, hay que reescribirlo como apartado formal

- [ ] **5. Objetivo General** — una sola oración, a partir de "4. Objetivo del
      MVP" (el párrafo que empieza "Que un agente vea...").
- [ ] **6. Objetivos específicos** — reformular las 5 condiciones del MVP
      (sección 4) como 4-6 objetivos con verbo en infinitivo (Detectar, Priorizar,
      Registrar, Medir...).
- [ ] **9. Partes interesadas del proyecto** — de la tabla de Usuarios (sección
      3), reencuadrada como stakeholders: agente, supervisor, administrador,
      cliente bancario (sujeto de los datos, no usuario), y el banco como
      organización. Agregar al equipo y al curso si la plantilla lo pide.
- [ ] **4. Justificación** — por qué vale la pena resolver esto (argumento de
      impacto/costo del fraude), distinto del "por qué NoSQL" técnico de la
      sección 6 — ese es insumo, no el apartado en sí.

## 3 · No existe, escritura nueva

- [ ] **1. Introducción** — contexto general, qué es Centinela en dos párrafos,
      cómo se organiza el resto del documento. Se escribe **al final**, cuando
      ya se sabe qué hay en los demás apartados.
- [ ] **7. Alternativa seleccionada** — un párrafo corto: "A. Desarrollo de
      software con MongoDB", con 2-3 líneas de por qué no B (el proyecto es
      transaccional/operativo, no analítico).
- [ ] **3. Antecedentes** — el más pesado de investigar. Necesita fuentes reales
      (no inventadas): estadísticas de fraude SINPE/vishing en Costa Rica (BCCR,
      SUGEF, noticias de bancos locales), y opcionalmente proyectos académicos
      o herramientas antifraude similares. **Definir el estilo de cita que pide
      la plantilla del aula antes de escribir esto** (IEEE numerado vs. otro).

## 4 · Bloqueado hasta que exista el trabajo técnico

- [ ] **10. Descripción de datos (BD, colecciones, documentos)** — **no se puede
      escribir bien hasta que H-01 (Práctica 1, 28 sep) esté terminado.** Ahí
      sale el esquema formal de las 11 colecciones con campos, tipos y ejemplo
      real — eso es literalmente el contenido de este apartado. Escribirlo antes
      solo produce algo genérico que hay que rehacer.

## 5 · Antes de subir

- [ ] Los 12 títulos son **exactamente** los del programa, sin renombrar.
- [ ] Ningún apartado vacío u omitido.
- [ ] Formato IEEE de la plantilla oficial (columnas, tipografía, encabezados).
- [ ] Revisión cruzada: alguien que NO escribió una sección la lee antes de
      subir — para que suene a una sola voz, no a 4 estilos pegados.
- [ ] Nomenclatura de archivo correcta, un archivo por cada uno de los 4.
- [ ] Cero rastro de IA en la redacción final.

Relacionado: [01 · Visión y alcance](01-vision-y-alcance.md),
[04 · Arquitectura](04-arquitectura.md), [03 · Historias de usuario](03-historias-usuario.md)
