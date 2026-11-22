# Centinela — Monitoreo de fraude SINPE en tiempo real

> Plataforma donde un agente de fraude ve las transferencias SINPE Móvil según
> ocurren, y actúa sobre las que el motor marca como probable estafa de vishing
> antes de que el dinero salga del sistema.

**Estado de la rama `dev`.** El detalle vivo de cada historia está en los
[issues](https://github.com/handelenriquezacuna/centinela/issues): una por historia
pendiente, que se cierra sola al fusionar su PR.

- **Hecho:** plataforma (H-00A, H-00B) con CI estricto, semilla y modo
  `CENTINELA_EXIGIR_MONGO`; esquema y validadores de las 13 colecciones (H-01, H-02);
  generador sintético con tope SINPE por entidad (H-03 a H-05); catálogo de reglas
  (H-07); evaluador (H-08); disparador D1 (H-09); medición de efectividad (H-10);
  API base y consulta de alertas (H-12, H-13 parcial); resolver una alerta (H-14);
  API de reglas y de política con versionado y simulación (H-16); portal con panel en
  vivo por SSE, pantalla de reglas y detalle de alerta (H-17, H-18); red de cuentas
  mula con `$graphLookup` (H-23, falta el grafo en el portal); alerta v2 con
  explicación embebida y migraciones reejecutables (H-26).
- **Pendiente:** H-06 parcial, H-11, H-15, H-19 a H-22, H-24, H-25, H-27.

Los contratos de datos y las reglas de arquitectura están en
[06 · Plataforma](docs/06-plataforma.md). **Leerlo antes de escribir un documento.**

## Cómo probarlo

```
python tareas.py instalar     # entorno con uv (Python 3.13, versiones del lock)
python tareas.py arriba       # replica set rsfraude en Docker
python tareas.py datos-demo   # siembra y conjunto de demo
python tareas.py dev          # API en http://127.0.0.1:8000/docs
```

Generador de datos sintéticos, como CLI aparte (con Mongo arriba):

```
uv run python -m app.generador --clientes 200 --transacciones 5000
```

Con la API corriendo, el portal está en:

- http://127.0.0.1:8000/panel (panel en vivo)
- http://127.0.0.1:8000/reglas (pantalla de reglas)

## El problema en una frase

En una estafa de vishing la víctima **autoriza** la transferencia. El banco no ve
un acceso no autorizado que bloquear: ve una operación legítima hecha por el
cliente correcto desde su propio teléfono. La única señal disponible es que **esa
transferencia no se parece a lo que esa persona hace normalmente**, y la ventana
para actuar se mide en minutos.

## Documentación

| Documento | Qué responde |
|---|---|
| [01 · Visión y alcance](docs/01-vision-y-alcance.md) | Qué es el MVP, para quién, qué queda fuera |
| [02 · Casos de uso](docs/02-casos-de-uso.md) | Los 10 flujos del sistema, con datos de ejemplo |
| [03 · Historias de usuario](docs/03-historias-usuario.md) | El backlog: 27 historias listas para tomar |
| [04 · Arquitectura](docs/04-arquitectura.md) | Cómo se conecta todo y cómo evoluciona por etapas |
| [05 · Infraestructura](docs/05-infraestructura.md) | Replica set, arranque, por qué no un mongod suelto |
| [06 · Plataforma](docs/06-plataforma.md) | **Los contratos que comparten los cuatro**: dinero, fechas, identificadores, versión de esquema, estados de la alerta, configuración, comandos |
| [07 · Plan de equipo](docs/07-plan-de-equipo.md) | **Cómo trabajamos**: reglas, flujo de ramas, quién toma qué, las tres entregas y el reparto del Avance 1 |

## Arquitectura visual

La evolución del sistema por etapas, como página navegable:
[`docs/arquitectura/index.html`](docs/arquitectura/index.html). Un solo lienzo
donde los componentes se encienden conforme avanzan las etapas E0 → E5.

Cuando el repo esté en GitHub, se publica con **Pages** apuntado a la carpeta
`/docs` de `main`. Queda en `https://<usuario>.github.io/<repo>/arquitectura/`.
Ya está el `.nojekyll` para que Pages sirva los archivos tal cual.

Para regenerar la página después de editar el contenido:

```
python3 scripts/build-pagina.py     # macOS y Linux
python scripts/build-pagina.py      # Windows
```

El fuente es `docs/arquitectura/_contenido.html`; el script le agrega doctype,
`charset` y `viewport`, necesarios para que la pagina funcione servida como
archivo estatico.

## Contexto académico

Proyecto final de **SC-609 Base de Datos NoSQL**, Universidad Fidélitas,
cuatrimestre set–dic 2026. Alternativa A. Equipo de 4.

| Hito | Fecha |
|---|---|
| Avance 1 — documento IEEE | lun 26 oct 2026 |
| Avance 2 — pantallas ejecutando | lun 16 nov 2026 |
| Entrega final + defensa | lun 7 dic 2026 |

La trazabilidad entre lo que construimos y lo que se califica vive en cada
historia de usuario, no en un checklist aparte.
