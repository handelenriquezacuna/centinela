# 01 · Visión y alcance

## 1. El problema

El vishing es una estafa telefónica donde el atacante se hace pasar por el banco,
convence a la víctima de que su dinero está en riesgo y la guía para que ella
misma lo transfiera a una "cuenta segura" que en realidad es una **cuenta mula**.

Lo que hace difícil este fraude:

| Característica | Consecuencia para el banco |
|---|---|
| La víctima **autoriza** la transferencia | No hay acceso no autorizado que bloquear |
| Se usa el dispositivo y las credenciales reales | Las defensas de identidad no disparan |
| SINPE Móvil liquida en segundos | La ventana de reacción son minutos, no días |
| El dinero se fragmenta en cadenas de mulas | Rastrear el destino final requiere seguir saltos |

La única señal aprovechable es de **comportamiento**: esa transferencia no se
parece a lo que esa persona hace normalmente.

## 2. Qué construimos

**Centinela** es un sistema de monitoreo que observa el flujo de transacciones,
las contrasta contra el perfil histórico de cada cliente y contra un conjunto de
reglas configurables, y **levanta alertas priorizadas** que un agente humano
resuelve desde un panel en tiempo real.

No es un sistema que bloquea automáticamente. Es un sistema que **le pone delante
al analista lo que importa, en el orden correcto, mientras todavía se puede hacer
algo**.

## 3. Usuarios

| Actor | Quién es | Qué necesita del sistema |
|---|---|---|
| **Agente de fraude** | Analista en el centro de monitoreo | Ver alertas nuevas al instante, decidir rápido, dejar rastro de por qué decidió |
| **Supervisor** | Coordina el turno | Ver la carga del equipo, ajustar reglas, medir cuánto se está atrapando |
| **Administrador** | Perfil técnico | Gestionar reglas, listas de riesgo y usuarios |
| **Cliente bancario** | La víctima potencial | *No usa el sistema.* Es el sujeto de los datos |
| **Motor de detección** | Proceso automático | Actor no humano: evalúa cada transacción y crea alertas |

## 4. Objetivo del MVP

> Que un agente vea una transferencia sospechosa aparecer sola en su panel, con
> el motivo por el que se marcó, y pueda resolverla dejando trazabilidad — todo
> sobre datos que se comportan como los reales.

Concretamente, el MVP está listo cuando estas cinco cosas son ciertas a la vez:

1. El generador produce transacciones con patrones realistas, incluyendo fraude.
2. El motor evalúa cada transacción y crea alertas con puntaje y motivo.
3. Las alertas aparecen en el panel **sin que el agente recargue** la página.
4. El agente puede resolver una alerta y queda registrado quién y cuándo.
5. El supervisor ve indicadores agregados del turno.

## 5. Alcance

### Dentro

- Ingesta de transacciones sintéticas a alto volumen.
- Perfil de comportamiento por cliente, recalculado periódicamente.
- Motor de reglas configurable en base de datos (no en código).
- Alertas con puntaje, motivo y ciclo de vida (`nueva → en_revisión → resuelta`).
- Panel en tiempo real vía WebSocket.
- Búsqueda forense sobre el histórico.
- Investigación de **redes de cuentas mula** siguiendo la cadena del dinero.
- Indicadores agregados del turno.
- Notificación por correo en riesgo crítico.
- Autenticación y roles.

### Fuera

| Fuera del alcance | Por qué |
|---|---|
| Bloqueo automático de transferencias | Decisión de negocio con impacto legal; el MVP asiste, no actúa solo |
| Integración con un core bancario real | No hay acceso; los datos son sintéticos por diseño |
| Modelos de machine learning | El valor está en el modelo de datos y las consultas, no en el clasificador |
| App móvil para el cliente | No aporta a la pregunta que resuelve el sistema |
| Datos personales reales | Prohibido y evitable: todo es generado |

## 6. Por qué NoSQL

Esta sección es la defensa del proyecto. No es decorativa.

| Razón | Cómo se manifiesta aquí |
|---|---|
| **Payload heterogéneo** | Una transferencia SINPE, un pago de servicios y una compra por datáfono comparten pocos campos. Modelarlas relacionalmente obliga a tablas dispersas o a EAV |
| **La transacción es un agregado** | Se escribe una vez, se lee completa, nunca se actualiza parcialmente. Es la definición de documento |
| **Volumen y escritura sostenida** | El flujo transaccional crece sin techo; el modelo documental escala horizontal |
| **La alerta lleva su historia adentro** | El rastro de auditoría se embebe: se lee siempre junto a la alerta y nunca por separado |
| **El esquema evoluciona** | Cada regla nueva agrega campos al documento de alerta sin migrar la colección entera |
| **Change Streams** | El tiempo real sale de la base de datos, no de un bus de mensajes aparte |

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| Datos sintéticos que no parecen reales y hacen trivial la detección | El generador modela población, horarios y estacionalidad; el fraude se inyecta como minoría (< 2 %) |
| El tiempo real se convierte en pozo de tiempo | El motor se construye **headless** primero: genera alertas correctas antes de que exista una sola pantalla |
| Frontend y backend divergen | El portal se sirve compilado desde la API; un solo proceso en la demo |
| Reparto desigual entre 4 personas | El backlog está en historias tomables con criterios de aceptación explícitos |
