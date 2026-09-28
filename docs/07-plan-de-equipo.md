# 07 · Plan de equipo

Este documento es para las cuatro personas del equipo. Dice cómo trabajamos, quién
toma qué, y en qué orden entrega el curso. Si algo de acá contradice lo que alguien
recuerda de una conversación, **manda este archivo**: está en el repo y se versiona.

Para levantar el ambiente y entender el monolito, ver
[06 · Plataforma](06-plataforma.md). Para tomar una historia,
[03 · Historias de usuario](03-historias-usuario.md).

## 1. Reglas del juego

No son preferencias de estilo. Cada una evita un problema que ya nos pasó o que
cuesta caro cuando pasa.

| Regla | Por qué |
|---|---|
| **Sin emojis** en código, commits, documentos ni comentarios | El repo lo revisa el profesor |
| **`main` es producción; nadie trabaja ni commitea ahí** | `main` debe contar la historia de las entregas, no la de los experimentos |
| **`dev` es el tronco de integración** | Todo converge ahí antes de llegar a `main` |
| **Una rama por historia**, `feature/H-XX-descripcion-corta`, sacada de `dev` | El código de la historia en el nombre hace que el PR se trace contra el backlog |
| **PR hacia `dev`, con CI verde** | El CI corre `ruff` y los cuatro tipos de prueba. Si falla, no se fusiona |
| **Mensajes de commit en español**, estilo `tipo(alcance): resumen` | `feat(semilla):`, `fix(infra):`, `docs:`, `test(portal):`. Sin emojis, sin líneas de coautor |
| **Documentarse antes de construir, sin sobre-ingeniería** | Si una práctica no mueve un rubro de la rúbrica ni reduce un riesgo real, no entra |

## 2. Flujo de ramas

```
main    o-----------------------o---------------------o     (solo entregas, con tag)
                               /                     /
dev     o--o--o--o--o--o--o--o-o--o--o--o--o--o--o--o-o     (integración)
         \     \        \
          \     \        feature/H-09-disparador-d1
           \     feature/H-07-reglas-catalogo
            feature/H-00A-andamio-plataforma
```

`main` recibe merge **solo tres veces**, en los tres hitos, y cada uno queda
etiquetado: `avance1`, `avance2`, `entrega-final`.

Un detalle de orden que importa: **H-01 se fusiona antes que H-07.** El catálogo de
reglas se valida contra los validadores `$jsonSchema` que crea H-01; al revés,
`scripts/semilla/03-catalogos.js` no tiene contra qué validar.

## 3. Quién toma qué

| Persona | Paquete | Historias |
|---|---|---|
| **A** | Datos | H-01 a H-06, H-24, H-26, H-27 |
| **B** | Detección | H-07 a H-11, H-21, H-23 |
| **C** | Portal y acceso | H-17 a H-20, H-22 **con su disparador D3**, H-25 |
| **D** | Plataforma y API núcleo | H-00A, H-00B, H-12 a H-16 |

**La propiedad es por revisor, no por dueño exclusivo.** Cualquiera implementa una
funcionalidad completa de punta a punta y pide revisión a quien revisa el módulo que
tocó. Lo único reservado a D es **cambiar un contrato compartido** — un tipo base, la
forma del error, el formato del evento SSE, la máquina de estados — porque eso afecta
a los otros tres a la vez.

| Módulo | Lo revisa |
|---|---|
| `config/`, `infra/`, `app/nucleo/`, `app/modelos/`, `app/repos/`, CI, `tareas.py` | D |
| `scripts/semilla/`, `app/generador/` | A |
| `app/deteccion/` | B |
| `scripts/procedimientos/` | A |
| `app/plantillas/`, `app/estaticos/` | C |

### Por qué el reparto cambia entre etapas

**No se asignan los cuatro paquetes fijos de principio a fin.** Funciona mientras el
trabajo es paralelo de verdad, pero revienta en la etapa del motor, que es una posta:
Detección alimenta a la API, y la API al Portal. Si cada quien se queda fijo en su
paquete, el Portal se pasa dos semanas esperando.

Lo que rompe la posta es que **el Portal y la API trabajan contra datos reales-en-forma
desde el día uno**, sin esperar al motor: `python tareas.py datos-demo` carga un
conjunto reproducible, y el canal SSE observa la colección `alertas`, así que da igual
quién escribió la alerta. Cuando el motor exista, es un cambio de fuente, no un
arranque de cero.

## 4. Las tres entregas y sus fechas

| Fecha | Qué se entrega | Qué exige |
|---|---|---|
| **12 oct** | Práctica 3 | Interfaz programada sobre NoSQL. La resuelve H-12 |
| **19 oct** | Práctica 4 | Sellos de versiones. La resuelve H-26 (`esquema_version` y la migración) |
| **26 oct** | **Avance 1** | Documento IEEE de 12 apartados. Narrativa, no código |
| **16 nov** | **Avance 2** | Pantallas ejecutando, con toma de decisiones. Acá sí aprieta el código |
| **7 dic** | **Entrega final y defensa** | App funcional, código en GitHub, IEEE final |

Dos cosas que conviene tener claras desde ahora. El **Avance 1 es puramente
narrativo**: el motor puede seguir en construcción esa semana. El **Avance 2 exige
código corriendo**, y es donde el calendario se pone real.

Y un rubro que se olvida hasta que es tarde: **que el proyecto corra sin un error en
tiempo de ejecución vale tanto como los disparadores y los procedimientos juntos.** La
última semana antes de la defensa es congelar y ensayar, no agregar funcionalidad.

## 5. El Avance 1, apartado por apartado

Doce apartados, y **los títulos son los de la plantilla del aula, tal cual**. Si un
título cambia, ese apartado no se califica.

El criterio de reparto fue que **nadie dependa del borrador de nadie**: cada apartado
se asignó a quien pueda completarlo con lo que ya está publicado en el repo.

| Persona | Apartados | De dónde sale |
|---|---|---|
| **A** | 9 Descripción para datos · 6 Alternativa seleccionada · 10 Tecnologías | El 9 sale del modelo que A misma construyó; los otros dos de `06-plataforma.md` |
| **B** | 3 Antecedentes · 5 Objetivos · 12 Referencias | Antecedentes usa fuentes externas (BCCR, SUGEF) y es autocontenido; Referencias se empareja con esas mismas fuentes |
| **C** | 2 Definición del problema · 8 Partes interesadas · 4 Justificación | Todo está en `01-vision-y-alcance.md` |
| **D** | 7 Solución planteada · 11 Arquitectura preliminar · 1 Introducción | La arquitectura está en `04-arquitectura.md`; la introducción se escribe con lo que ya existe |

**La redacción final no se reparte: D ensambla y valida.** Repartir "un párrafo cada
uno" sale descosido, y el documento se lee como lo que es.

Dos advertencias de la rúbrica: **usar IA para redactar es causal de nulidad**, igual
que no usar el formato IEEE. Y **cada integrante sube el mismo documento** con su
nombre en la nomenclatura del archivo; no lo sube una sola persona por el grupo.

## 6. Antes de pedir revisión

```
python tareas.py verificar
```

Corre las compuertas que después va a correr el CI. Si eso pasa en tu máquina, el PR
no se va a caer por algo que podías haber visto antes.

Y la regla que no se rompe: **ningún agente ni herramienta commitea ni empuja.** Quien
commitea es una persona, después de mirar el diff.
