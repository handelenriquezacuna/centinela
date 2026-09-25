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
      (`SC609`) y `LN` no está explicado. Preguntar en clase.
- [ ] Confirmar el número de grupo (`GX`) asignado por el profesor.
- [ ] Recordar: **cada uno de los 4 sube el mismo documento** con su nombre en
      la nomenclatura — no lo sube solo una persona por el grupo.
- [ ] Citas y referencias: por defecto usar la **guía APA7 de Fidélitas**
      (`conceptos/apa7-fidelitas.md` del cerebro) — es la política general de
      la universidad y ya está corregida y verificada contra el manual oficial.
      Si la plantilla IEEE del aula pide un estilo distinto, ese gana.

## 1 · Reparto — 12 apartados ÷ 4 personas, nadie depende de nadie

Regla de división: cada persona recibe apartados cuya fuente **ya existe hoy en
el repo** (`01-vision-y-alcance.md`, `04-arquitectura.md`, la plantilla de grupo
llena) — nadie necesita el borrador de otro compañero para arrancar el suyo.
El único apartado con una dependencia real (10) depende de un **hito técnico
compartido** (Práctica 1, en el que ya trabajan los 4 juntos), no de una persona
específica — se explica abajo.

### Persona A — dueña de los datos (sigue con H-03–H-06 después de Práctica 1)

- [ ] **10. Descripción de datos (BD, colecciones, documentos)** — el único con
      dependencia real: el contenido final sale de H-01 (Práctica 1, 28 sep).
      **No hay que esperar de brazos cruzados**: A ya es parte del equipo que
      construye H-01, así que puede escribir la estructura del apartado *ahora*
      usando `antifraude-modelo-datos.md` (las 11 colecciones ya están nombradas
      y descritas a alto nivel) y solo rellenar los campos/tipos/ejemplo exacto
      una vez que el script de Práctica 1 esté listo — no depende de que otro
      compañero le entregue nada.
- [ ] **7. Alternativa seleccionada** — un párrafo corto: "A. Desarrollo de
      software con MongoDB", con 2-3 líneas de por qué no B.
- [ ] **11. Tecnologías que se utilizarán** — de la plantilla de grupo ya llena
      (FastAPI, Motor, React, Tailwind, Docker, Faker, Mailtrap, GitHub). Es casi copiar.

### Persona B — investigación y objetivos

- [ ] **3. Antecedentes** — el apartado más pesado, pero 100% autocontenido:
      fuentes externas (BCCR, SUGEF, noticias de fraude SINPE/vishing en Costa
      Rica), no depende de nada del equipo. Arrancar temprano por ser el más largo.
- [ ] **5. Objetivo General** — una sola oración, a partir de "4. Objetivo del
      MVP" en `01-vision-y-alcance.md` (el párrafo que empieza "Que un agente vea...").
- [ ] **6. Objetivos específicos** — reformular las 5 condiciones del MVP
      (misma sección) como 4-6 objetivos con verbo en infinitivo (Detectar,
      Priorizar, Registrar, Medir...).

### Persona C — problema y partes interesadas

- [ ] **2. Definición del problema** — de "1. El problema" en
      `01-vision-y-alcance.md` (vishing, por qué el banco no puede blindarse con identidad).
- [ ] **9. Partes interesadas del proyecto** — de la tabla de Usuarios (sección
      3 del mismo doc), reencuadrada como stakeholders: agente, supervisor,
      administrador, cliente bancario (sujeto de los datos, no usuario), el banco.
- [ ] **4. Justificación** — por qué vale la pena resolver esto (impacto/costo
      del fraude), distinto del "por qué NoSQL" técnico (sección 6 del mismo doc)
      — ese es insumo, no el apartado en sí.

### Persona D — solución, arquitectura e introducción

- [ ] **8. Solución planteada** — de "2. Qué construimos" en `01-vision-y-alcance.md`.
- [ ] **12. Arquitectura preliminar** — de `04-arquitectura.md`. **Exportar los
      diagramas Mermaid a imagen** (captura de pantalla renderizada en GitHub, o
      recrearlos en draw.io/Excalidraw) — Word no renderiza Mermaid. Usar el
      diagrama de Contenedores (sección 3) y el ER de decisiones de modelado
      (sección 5) como mínimo — ya cubren el rubro 2 (documentación con diagramas).
- [ ] **1. Introducción** — se puede escribir un v1 completo **hoy mismo**, solo
      con `01-vision-y-alcance.md` (ya cubre problema, solución, alcance y por
      qué NoSQL). No depende de que A/B/C terminen; al final del proceso alguien
      (no necesariamente D) le da una pasada de coherencia junto con el resto.

**Balance de carga**: A y D quedan con 1 apartado mediano/pesado + 2 livianos;
B queda con el más pesado (Antecedentes) pero sus otros 2 son triviales; C
queda con 3 medianos parejos. Ningún apartado de una persona necesita el
producto terminado de otra — todos parten de lo que ya está publicado en el repo.

## 2 · Bloqueado hasta que exista el trabajo técnico

- [ ] **10 (Persona A)** — solo el detalle fino de campos/tipos/ejemplo espera a
      que H-01 (Práctica 1, 28 sep) esté terminado. La estructura y el resto de
      apartados se pueden escribir completos desde ya.

## 3 · Antes de subir

- [ ] Los 12 títulos son **exactamente** los del programa, sin renombrar.
- [ ] Ningún apartado vacío u omitido.
- [ ] Formato IEEE de la plantilla oficial (columnas, tipografía, encabezados).
- [ ] Revisión cruzada: alguien que NO escribió una sección la lee antes de
      subir — para que suene a una sola voz, no a 4 estilos pegados.
- [ ] Nomenclatura de archivo correcta, un archivo por cada uno de los 4.
- [ ] Cero rastro de IA en la redacción final.

Relacionado: [01 · Visión y alcance](01-vision-y-alcance.md),
[04 · Arquitectura](04-arquitectura.md), [03 · Historias de usuario](03-historias-usuario.md)
