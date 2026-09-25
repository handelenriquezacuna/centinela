# 06 · Checklist del Avance 1

Documento **IEEE** de 12 apartados (Alternativa A). **Vence lunes 26 de octubre.**
No omitir ni renombrar ningún apartado — si un título cambia, ese apartado no
se califica. Se redacta a mano: usar IA aquí es causal de nulidad.

**Títulos oficiales (de la plantilla real del aula, no del programa impreso —
son estos, tal cual, sin cambiar una palabra):**

1. INTRODUCCIÓN
2. DEFINICIÓN DEL PROBLEMA
3. ANTECEDENTES
4. JUSTIFICACIÓN
5. OBJETIVOS
6. ALTERNATIVA SELECCIONADA
7. SOLUCIÓN PLANTEADA
8. PARTES INTERESADAS DEL PROYECTO
9. DESCRIPCIÓN PARA DATOS
10. TECNOLOGÍAS QUE UTILIZAN
11. ARQUITECTURA PRELIMINAR DE LA SOLUCIÓN
12. REFERENCIAS

> Dos diferencias frente a la lista del programa impreso que usábamos antes:
> **OBJETIVOS es un solo apartado** (general + específicos juntos, con
> subtítulos adentro, no dos apartados separados), y **REFERENCIAS es nuevo**
> — no estaba en el PDF del programa.

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
El único apartado con una dependencia real (9) depende de un **hito técnico
compartido** (Práctica 1, en el que ya trabajan los 4 juntos), no de una persona
específica.

### Persona A — dueña de los datos (sigue con H-03–H-06 después de Práctica 1)

- [ ] **9. DESCRIPCIÓN PARA DATOS** (BD, colecciones, documentos) — el único con
      dependencia real: el contenido final sale de H-01 (Práctica 1, 28 sep).
      A ya es parte del equipo que construye H-01, así que puede escribir la
      estructura del apartado *ahora* con `antifraude-modelo-datos.md` (las 11
      colecciones ya están nombradas y descritas) y solo rellenar los campos/
      tipos/ejemplo exacto cuando el script de Práctica 1 esté listo.
- [ ] **6. ALTERNATIVA SELECCIONADA** — un párrafo corto: "A. Desarrollo de
      software con MongoDB", con 2-3 líneas de por qué no B.
- [ ] **10. TECNOLOGÍAS QUE UTILIZAN** — de la plantilla de grupo ya llena
      (FastAPI, Motor, React, Tailwind, Docker, Faker, Mailtrap, GitHub). Casi copiar.

### Persona B — investigación, objetivos y referencias

- [ ] **3. ANTECEDENTES** — el apartado más pesado, pero 100% autocontenido:
      fuentes externas (BCCR, SUGEF, noticias de fraude SINPE/vishing en Costa
      Rica). Arrancar temprano por ser el más largo.
- [ ] **5. OBJETIVOS** — un solo apartado con dos partes adentro: Objetivo
      General (una oración, de "Objetivo del MVP" en `01-vision-y-alcance.md`)
      y Objetivos específicos (4-6, reformulando las 5 condiciones del MVP con
      verbo en infinitivo: Detectar, Priorizar, Registrar, Medir...).
- [ ] **12. REFERENCIAS** — se empareja bien con Antecedentes: mismas fuentes,
      formateadas como lista bibliográfica APA7. Se arma en paralelo con las
      fuentes que B ya está reuniendo para Antecedentes; solo necesita una
      pasada final de 10 minutos para sumar alguna cita suelta que A/C/D hayan
      usado en su apartado (ej. documentación de MongoDB) — no bloquea a nadie
      durante la escritura, es housekeeping de cierre.

### Persona C — problema y partes interesadas

- [ ] **2. DEFINICIÓN DEL PROBLEMA** — de "1. El problema" en
      `01-vision-y-alcance.md` (vishing, por qué el banco no puede blindarse
      con identidad).
- [ ] **8. PARTES INTERESADAS DEL PROYECTO** — de la tabla de Usuarios (sección
      3 del mismo doc), reencuadrada como stakeholders: agente, supervisor,
      administrador, cliente bancario (sujeto de los datos, no usuario), el banco.
- [ ] **4. JUSTIFICACIÓN** — por qué vale la pena resolver esto (impacto/costo
      del fraude), distinto del "por qué NoSQL" técnico (sección 6 del mismo doc)
      — ese es insumo, no el apartado en sí.

### Persona D — solución, arquitectura e introducción

- [ ] **7. SOLUCIÓN PLANTEADA** — de "2. Qué construimos" en `01-vision-y-alcance.md`.
- [ ] **11. ARQUITECTURA PRELIMINAR DE LA SOLUCIÓN** — de `04-arquitectura.md`.
      **Exportar los diagramas Mermaid a imagen** (captura de pantalla
      renderizada en GitHub, o recrearlos en draw.io/Excalidraw) — Word no
      renderiza Mermaid. Usar el diagrama de Contenedores (sección 3) y el ER
      de decisiones de modelado (sección 5) como mínimo — ya cubren el rubro 2
      (documentación con diagramas).
- [ ] **1. INTRODUCCIÓN** — se puede escribir un v1 completo **hoy mismo**, solo
      con `01-vision-y-alcance.md` (ya cubre problema, solución, alcance y por
      qué NoSQL). No depende de que A/B/C terminen; al final del proceso alguien
      le da una pasada de coherencia junto con el resto.

**Balance de carga**: A y D quedan con 1 apartado mediano/pesado + 2 livianos;
B carga con el más pesado (Antecedentes) pero Objetivos y Referencias son
livianos y se apoyan en el mismo trabajo; C queda con 3 medianos parejos.
Ningún apartado de una persona necesita el producto terminado de otra durante
la escritura — todos parten de lo que ya está publicado en el repo.

## 2 · Bloqueado hasta que exista el trabajo técnico

- [ ] **9 (Persona A)** — solo el detalle fino de campos/tipos/ejemplo espera a
      que H-01 (Práctica 1, 28 sep) esté terminado. La estructura y el resto de
      apartados se pueden escribir completos desde ya.

## 3 · Validación IEEE — paso explícito antes de ensamblar

No es un checkbox suelto al final: es una pasada dedicada, después de que los
4 apartados de cada quien estén escritos, **contra la plantilla real** (no de
memoria). Se hace una sola vez, sobre el documento ya unido — por eso es la
única actividad que sí depende de que los 4 hayan terminado, y es intencional:
es ensamblaje, no escritura.

- [ ] Los 12 títulos son **idénticos, carácter por carácter**, a la lista de
      arriba (mayúsculas incluidas) — comparar contra la plantilla descargada,
      no contra este checklist ni contra el programa impreso.
- [ ] Numeración de apartados correlativa 1-12, sin saltos ni duplicados.
- [ ] Formato de página de la plantilla: márgenes, tipografía, interlineado,
      numeración de página — igual en los 4 documentos individuales (los 4 suben
      el mismo archivo, pero un copy-paste manual puede desalinear el formato).
- [ ] OBJETIVOS trae sus dos subpartes (General y Específicos) claramente
      diferenciadas adentro del apartado 5 — no como dos apartados sueltos.
- [ ] REFERENCIAS incluye **todas** las fuentes citadas en cualquier apartado
      (no solo las de Antecedentes), en formato APA7, orden alfabético.
- [ ] Cada figura/tabla (diagramas del apartado 11) está numerada y citada en
      el cuerpo del texto antes de aparecer, por regla APA7/Fidélitas.
- [ ] Revisión cruzada de contenido: alguien que NO escribió una sección la lee
      antes de subir — para que suene a una sola voz, no a 4 estilos pegados.
- [ ] Nomenclatura de archivo correcta, un archivo por cada uno de los 4.
- [ ] Cero rastro de IA en la redacción final.

Relacionado: [01 · Visión y alcance](01-vision-y-alcance.md),
[04 · Arquitectura](04-arquitectura.md), [03 · Historias de usuario](03-historias-usuario.md)
