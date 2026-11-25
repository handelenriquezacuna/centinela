# 11 · Diseño de la interfaz por rol (T-74, fase de diseño)

Estado: **propuesta para aprobación de Handel** (D-75: primero diseño, después construcción).
Rama `feature/H-17-ux-por-rol`, de `dev` 535948f. Este documento no cambia código.

Método: se construyó la SPA, se levantó un servidor (puerto 8160) contra un nodo de prueba
sembrado (01, 02, 03, 05), con el demo y 2.000 transacciones del generador (26 alertas, 16
cuentas destino con alertas abiertas), y se recorrió cada pantalla con cada rol a 1366x900
y a 390x844. Las capturas citadas están en `docs/ux-capturas/` (nombre
`<rol>-<pantalla>-<d|m>.png`; `d` = escritorio, `m` = teléfono).

Lo que dicen los perfiles sobre frecuencia y presión de tiempo son **supuestos de diseño**,
no mediciones: el proyecto no tiene usuarios reales. Lo que dicen las capturas es observado.

## 1. Perfiles

| | Agente de fraude (primera línea) | Supervisor | Administrador |
|---|---|---|---|
| Qué decide | Tomar una alerta y resolverla: confirmar, descartar o escalar; si seguir el dinero | Qué se escala, qué cuentas entran a la lista de riesgo, si una regla o los cortes de severidad se ajustan | Que el sistema esté sano y recuperable: respaldos, quién entra al portal |
| Frecuencia | Continua, toda la jornada; decenas de alertas por turno | Varias veces al día: al inicio (qué hay pendiente), a mitad (propuestas de mula), al cierre (efectividad) | Baja: una vez al día mira la salud; un respaldo antes de cambios o por calendario |
| Presión de tiempo | Alta: una transferencia SINPE Móvil fraudulenta no se revierte; cada minuto de la cola cuenta | Media-alta: lo que se le escala sí tiene hora | Baja en el día normal; muy alta en un incidente |
| Permisos (`permisos.py`) | panel, detalle, resolver, buscar, red de mulas | lo del agente + tablero, ver/editar reglas, gestionar listas | todos (incluye lo que se agregue, como respaldos) |

Qué les duele hoy, con evidencia:

| Rol | Dolor | Evidencia |
|---|---|---|
| Agente | Para resolver una alerta hay que bajar ~830 px: la barra de decisión ("Tomar") está al final de la página, bajo transacción, perfil, reglas y política. El botón está en y=1731 en una ventana de 900 | `agente-detalle-completo-d.png`, `agente-detalle-d.png` |
| Agente | Entra y aterriza en el panel, bien, pero al abrir una alerta pierde de vista la cola: no hay siguiente/anterior | `agente-panel-casos-d.png` |
| Agente | En teléfono la tabla "Por alerta" se corta a la derecha (columna Cliente) sin pista de que hay más; la cabecera ocupa ~140 de 844 px | `admin-panel-alertas-m.png` |
| Agente | Si abre `/tablero` ve "Acceso denegado / Tu rol no permite esta operación" sin saber qué rol hace falta ni adónde ir | `agente-tablero-d.png` |
| Supervisor | Aterriza en el panel de alertas, no en lo que le importa (pendientes, antigüedad). El tablero está a un clic pero entre "Búsqueda" y "Red de mulas" | `supervisor-tablero-d.png` |
| Supervisor | Tablero con ruido: "sin periodo anterior con que comparar" se repite 5 veces; los KPI dicen "sin resueltas" en tipografía gigante en lugar de un estado vacío | `supervisor-tablero-d.png`, `supervisor-tablero-m.png` |
| Supervisor | Formulario de política desordenado: el fieldset de cortes estira la fila y deja "Umbral" y "Puntaje máximo" abajo, lejos del título. La ayuda habla de "un 409" | `supervisor-reglas-d.png`, `supervisor-reglas-m.png` |
| Supervisor | Red de mulas pide escribir una cuenta; no hay lista de "propuestas pendientes" para aprobar | `agente-red-d.png` |
| Administrador | Ve exactamente la misma navegación que el supervisor; no hay Administración. Salud (`/estado`) es JSON crudo. Usuarios solo por `tareas.py usuarios`. Respaldos no existen | `admin-estado-d.png`, `admin-panel-alertas-m.png` |
| Todos | Un enlace a una ruta que la SPA no conoce, al recargar (p. ej. `/respaldos`), devuelve JSON `{"error":...}` del servidor en vez de la pantalla "no encontrada" | `admin-respaldos-404-d.png` |

## 2. Recorrido del día por rol

Los clics cuentan desde la pantalla de aterrizaje y no incluyen desplazamiento; el
desplazamiento se anota aparte porque es parte del problema.

### Agente

| Trabajo | Hoy | Debería ser |
|---|---|---|
| 1. Atender la alerta más crítica | Panel -> clic en la alerta (1) -> **2 desplazamientos** -> Tomar (2) -> Confirmar/Descartar/Escalar (3) -> diálogo (4). 4 clics + 2 scrolls | Barra de decisión fija arriba del detalle. 3 clics, 0 scroll. Tras resolver: "Siguiente alerta" (1 clic) en vez de volver a la cola |
| 2. Resolver una cuenta destino con varias víctimas | Marcar casillas en la tarjeta + "Tomar" (2 clics); ya existe, y es lo mejor del panel | Igual, más un "Seleccionar todas" visible y un resultado anunciado ("3 alertas tomadas") |
| 3. Seguir el dinero | Detalle -> "ver red de mulas" (1 clic). Bien | Igual; el Sankey se abre con el origen resaltado |
| 4. Buscar histórico (forense) | `/busqueda`, 2-4 filtros, Buscar, Exportar CSV. Sin filtros guardados; el filtro de cuenta acepta origen o destino | Igual. Añadir enlace de cada fila a su alerta (ya hay insignia) y mantener filtros en la URL |
| 5. Notar alertas nuevas | Canal en vivo + aviso en `aria-live`. Bien | Igual; que el aviso diga cuántas llegaron y permita aplicarlas con un clic |

### Supervisor

| Trabajo | Hoy | Debería ser |
|---|---|---|
| 1. Ver qué está pendiente al empezar | Aterriza en Panel; Tablero (1 clic), leer KPI entre ruido | Aterriza en el Tablero con una franja "Pendientes: 23, la más antigua hace X" y enlace a la cola filtrada. 0 clics |
| 2. Aprobar propuestas de mula | Red de mulas, escribir cuenta, Ver red, localizar propuesta (3+ clics, sin lista global) | Bandeja "Propuestas pendientes" en el inicio o en Red de mulas; aprobar/rechazar desde ahí (2 clics) con confirmación |
| 3. Ajustar una regla o los cortes | Reglas -> editar -> Simular -> Guardar (4 clics), form desordenado | Mismo flujo; formulario ordenado, simulación visible junto al botón Guardar, errores 409 con lenguaje humano |
| 4. Revisar efectividad del equipo | Tablero, bajar a Efectividad | Mismo, con periodo en la URL y estados vacíos claros |
| 5. Intervenir en una alerta | Igual que el agente, con la misma barra de decisión | Igual |

### Administrador

| Trabajo | Hoy | Debería ser |
|---|---|---|
| 1. ¿El sistema está sano? | No hay pantalla; `/estado` es JSON | Aterriza en Salud del sistema: estado de Mongo, réplica, D1 y rezago, último respaldo. 0 clics |
| 2. Crear un respaldo | No existe | Administración -> Respaldos -> Crear respaldo (3 clics) |
| 3. Verificar un respaldo | No existe | En la fila, "Verificar" (1 clic), resultado en la misma fila |
| 4. Dar de alta o baja un usuario | Solo `python tareas.py usuarios` | Usuarios del portal (lista de solo lectura en v1; alta/baja cuando exista contrato HTTP) |
| 5. Trabajo de supervisor | Mismo menú | Mismo menú, bajo el área "Operación" |

## 3. Problemas encontrados, por impacto

| # | Impacto | Categoría | Problema | Evidencia |
|---|---|---|---|---|
| 1 | Alto | Jerarquía visual | La acción principal del detalle (decidir la alerta) está al final de ~2.000 px. Es la tarea más frecuente del rol más frecuente | `agente-detalle-completo-d.png` |
| 2 | Alto | Navegación | Cada rol ve el mismo menú plano; el administrador no tiene su área; "Red de mulas" se añade a mano en `Marco.tsx` porque la lista `NAVEGACION` del servidor no la trae, por eso queda siempre al final y fuera de orden de trabajo | `admin-panel-alertas-m.png`, `Marco.tsx` |
| 3 | Alto | Navegación | Pantalla de inicio igual para todos (`/` -> `/panel`) | `App.tsx` |
| 4 | Alto | Teléfono | Cabecera de 3 filas (~140 px, 17 % de la pantalla), tabla de alertas que se corta sin pista, formularios de varias columnas | `agente-panel-casos-m.png`, `admin-panel-alertas-m.png` |
| 5 | Medio | Errores | "Acceso denegado" sin rol requerido ni salida; recarga de ruta desconocida devuelve JSON | `agente-tablero-d.png`, `admin-respaldos-404-d.png` |
| 6 | Medio | Estados vacíos | KPI vacíos como "sin resueltas" en titular; "sin periodo anterior con que comparar" repetido; "Esta cuenta no tiene salidas registradas en la ventana" sin siguiente paso | `supervisor-tablero-d.png`, `agente-detalle-d.png` |
| 7 | Medio | Consistencia | Cada pantalla reimplementa carga, error y vacío (`<p role="status">Cargando…</p>` en 7 sitios); tarjetas, insignias y tablas por pantalla; `Aviso` solo en `reglas/` | grep en `paginas/`, `reglas/` |
| 8 | Medio | Textos | "un 409", "D1", "Canal: en vivo", ids crudos (`AGT-001 (agente)` en vez de nombre y rol); "Tu rol no permite esta operación" | `supervisor-reglas-d.png`, `agente-panel-casos-d.png` |
| 9 | Medio | Jerarquía visual | Formulario de política: el fieldset de cortes estira la fila. En el detalle, "Puntaje total" y "Política y cortes" casi se tocan | `supervisor-reglas-d.png`, `agente-detalle-completo-d.png` |
| 10 | Bajo-medio | Accesibilidad | Lo bueno: foco al contenido al cambiar de ruta, `<dialog>` modal, regiones `aria-live`. Falta: tabla del panel sin leyenda de columnas ordenables, color como único canal en severidad del panel por alerta (la insignia ya lleva texto), sin enlace "saltar al contenido", objetivos táctiles de 34 px de alto | `Marco.tsx`, capturas `m` |
| 11 | Bajo | Consistencia | Login: formulario en una fila sin título de página, el error se detecta solo por prueba; el nombre de la persona no aparece en ningún lado | `anon-login-d.png` |

## 4. Arquitectura de interfaz propuesta

### 4.1 Navegación por rol

La navegación es de una sola fuente: el servidor ya la entrega en `GET /api/v1/sesion`
(`navegacion` y `permisos`). Propuesta: que la SPA agrupe por área según el permiso y que
`NAVEGACION` incluya Red de mulas y las páginas de administración. **Eso toca el contrato de
la sesión y los permisos: lo hace el arquitecto** (ver sección 5).

| Menú | Agente | Supervisor | Administrador |
|---|---|---|---|
| Inicio (aterriza en) | Cola (`/panel`) | Tablero con pendientes (`/tablero`) | Salud del sistema (`/administracion/salud`) |
| Alertas: Panel | sí | sí | sí |
| Alertas: Búsqueda | sí | sí | sí |
| Alertas: Red de mulas | sí (consulta) | sí (consulta + aprobar y baja) | sí (igual) |
| Supervisión: Tablero | no | sí | sí |
| Supervisión: Reglas y política | no | sí (editar, simular, crear) | sí |
| Administración: Salud del sistema | no | no | sí |
| Administración: Respaldos | no | no | sí |
| Administración: Usuarios del portal | no | no | sí |

Detalles: el menú agrupa por área en pantalla ancha (Alertas / Supervisión / Administración);
en teléfono se vuelve un botón "Menú" que despliega la lista (una sola fila de cabecera). El
nombre de la persona y el rol aparecen en el menú de usuario, junto a "Salir".

### 4.2 Pantalla de inicio por rol

`/` deja de ser un `Navigate` fijo: lee la sesión y redirige a la ruta de inicio del rol
(tabla anterior). Cada rol puede volver a su inicio con el logotipo.

### 4.3 Catálogo de módulos reutilizables

Existe hoy en `frontend/src/componentes/`: `Marco` (cabecera y `<main>` con foco),
`RequiereSesion`, `DialogoConfirmacion` (ya accesible, se queda), `AvisoRezago`,
`AyudaAtajos`, `EncabezadoPanel` (específico del panel), `Sankey`, `GrafoRed`,
`RecorridoDinero`. Existen también `reglas/Aviso` (resultado ok/error), `tablero/Variacion`
y `formatos.ts` (`colones`, `horaCostaRica`, `porcentaje`).

A extraer o crear (todos en `frontend/src/componentes/`, con prueba de Vitest propia):

| Módulo | Props principales | Dónde se usa | Origen |
|---|---|---|---|
| `MarcoPagina` | `children`, `ancho?: "normal" \| "ancho"` | Todas las pantallas (envoltura de `<section>` con título enlazado por `aria-labelledby`) | Nuevo |
| `EncabezadoPagina` | `titulo`, `descripcion?`, `acciones?`, `migas?` | Todas; sustituye `titulo-pantalla` suelto y `EncabezadoPanel` | Extrae de `EncabezadoPanel`, `Detalle` |
| `TarjetaMetrica` | `rotulo`, `valor`, `variacion?`, `estado?: "normal" \| "sin_datos"` | Tablero, panel (resumen de cuenta), Salud | Extrae de `Kpis.tsx` |
| `TablaDatos<T>` | `columnas`, `filas`, `clave`, `orden?`, `alOrdenar?`, `paginacion?`, `estado: cargando \| error \| vacia \| listo`, `vacia?: ReactNode`, `alReintentar?` | Panel por alerta, Búsqueda, Reglas, Respaldos, Usuarios | Extrae de `PanelAlertas`, `Busqueda` |
| `Insignia` | `tipo: "severidad" \| "estado" \| "info"`, `valor` | Panel, detalle, búsqueda, respaldos (estado de verificación) | Extrae las clases de insignias dispersas |
| `DialogoConfirmacion` | (sin cambios) | Detalle, Red, Recorrido, Respaldos | Ya existe |
| `FormularioCampo` | `etiqueta`, `ayuda?`, `error?`, `children` | Login, Reglas, Búsqueda, Red, Respaldos | Extrae de `Politica`, `NuevaRegla`, `Busqueda` |
| `AvisoRegion` | `resultado: {tipo, texto} \| null`, `politeness?` | Todas las acciones con resultado | Generaliza `reglas/Aviso.tsx` |
| `Permitido` | `permiso`, `children`, `alternativa?` | Menú, botones de gestionar listas, editar reglas, Administración | Nuevo; usa `sesion.permisos` |
| `FiltroBarra` | `campos`, `valores`, `alCambiar`, `alLimpiar`, `colapsable?` | Búsqueda, Tablero (periodo), Respaldos (filtro de estado) | Extrae de `Busqueda`, selector de periodo del `Tablero` |
| `VistaVacia` | `titulo`, `texto`, `accion?` | Panel sin alertas, búsqueda sin resultados, KPI sin datos, respaldos sin copias | Nuevo; reemplaza `cola-vacia` y los textos "sin ..." |
| `VistaError` | `error: MensajeError`, `alReintentar?` | Todas | Nuevo; centraliza el mapeo de errores de `reglas/errores.ts` |
| `MontoColones` | `valor: number` | Panel, detalle, búsqueda, tablero | Envuelve `formatos.colones` |
| `FechaCR` | `valor`, `formato?: "completo" \| "corto" \| "relativo"` | Todas las que muestran fecha | Envuelve `formatos.horaCostaRica`; `<time dateTime>` con el ISO UTC |
| `MenuPrincipal` | `sesion` | Dentro de `Marco` | Extrae de `Marco.tsx`; agrupa por área, versión móvil colapsable |
| `BarraDecision` | `alerta`, `acciones`, `alResolver` | Detalle (parte superior, fija), reusable en lote del panel | Extrae de la tarjeta "Resolver alerta" de `Detalle.tsx` |

Regla: ninguna pantalla vuelve a escribir su propio "Cargando…", "Sin datos" o `<table>`; si
falta una variante, se agrega al módulo.

### 4.4 Pantalla de Respaldos (solo administrador)

Ruta `/administracion/respaldos`, dentro de `MarcoPagina` + `EncabezadoPagina`. Depende de la
API que construye el otro agente; abajo va el diseño y lo que la pantalla necesita de ella
(si el contrato difiere, se ajusta la pantalla, no se inventa otro).

Cabecera: título "Respaldos", descripción "Copias de la base `antifraude`. Restaurar no se
hace desde el portal", y a la derecha el botón primario **Crear respaldo**.

Tabla (`TablaDatos`, orden por fecha descendente):

| Columna | Contenido |
|---|---|
| Fecha (hora CR) | `FechaCR` completo |
| Creado por | nombre y id del administrador, o "Programado" |
| Tamaño | legible (KB/MB) |
| Documentos | total, y por colección en un detalle desplegable (13 colecciones) |
| Verificación | `Insignia`: Sin verificar / Verificando / Verificado / Falló, con fecha de la última verificación |
| Acciones | **Verificar** |

Comportamiento:

| Acción | Resultado visible |
|---|---|
| Crear respaldo | El botón queda deshabilitado con "Creando…"; el resultado se anuncia en `AvisoRegion` ("Respaldo creado: 2.157 documentos, 3,2 MB") y la fila nueva aparece arriba |
| Verificar | Sin diálogo (es de solo lectura). La insignia pasa a "Verificando…" y luego a "Verificado" o "Falló"; el detalle de la verificación (documentos esperados contra encontrados, por colección) se abre en la misma fila |
| Lista vacía | `VistaVacia`: "Todavía no hay respaldos. Crea el primero antes de cambiar reglas o actualizar." con el botón Crear |
| Cargando / error de lista | `TablaDatos` en estado cargando; error con `VistaError` y "Reintentar" |

Mensajes si falla (en español, sin códigos HTTP, con qué hacer):

| Situación | Mensaje |
|---|---|
| Crear falla (Mongo no responde) | "No se pudo crear el respaldo: la base no respondió. Revisa Salud del sistema y vuelve a intentar." |
| Crear falla por espacio o permisos | "No se pudo escribir el respaldo en disco: [motivo del servidor]. No se guardó nada." |
| Crear mientras ya hay uno en curso | "Ya hay un respaldo en curso. Espera a que termine." |
| Verificar: no coinciden conteos | "El respaldo no coincide con lo registrado: [colección] tiene N de M documentos. No lo uses para restaurar." |
| Verificar: archivo ausente o ilegible | "No se encontró o no se pudo leer el archivo del respaldo. Crea otro." |
| Sesión vencida | Ya la cubre el cliente: vuelve al login conservando el destino |

**Por qué no hay botón Restaurar** (D-74): restaurar reemplaza la base viva; un clic
accidental, o una sesión robada, borraría los datos de operación del fraude con el sistema
en marcha (D1 y el canal SSE leyendo). Es una operación de recuperación, rara y deliberada:
se hace con el servicio detenido, desde un terminal con acceso a la máquina. La pantalla lo
dice y muestra el comando para copiar, con el identificador del respaldo ya rellenado:

```
python tareas.py <comando-de-restauración> <id-del-respaldo>
```

El nombre exacto del comando lo fija quien construye el backend de respaldos (a la fecha de
este diseño `tareas.py` no tiene ninguna tarea de respaldo ni de restauración). La tarjeta
"Cómo restaurar" muestra: 1) detener el servidor, 2) correr el comando, 3) volver a
arrancar y comprobar en Salud del sistema. Con botón "Copiar comando".

### 4.5 Salud del sistema y Usuarios del portal

- **Salud del sistema** (`/administracion/salud`): `TarjetaMetrica` para Mongo (conectado,
  réplica, primario), D1 (rezago, ya existe `/api/v1/deteccion/rezago`), último respaldo y su
  verificación. Alimentada por `GET /estado` (hoy JSON crudo, existente, cualquier sesión) y
  `/api/v1/deteccion/rezago`; no requiere contrato nuevo salvo el último respaldo.
- **Usuarios del portal** (`/administracion/usuarios`): no existe API de agentes. v1 propuesta:
  pantalla informativa con el comando de `tareas.py usuarios` (como Respaldos hace con
  restaurar) hasta que el arquitecto publique un contrato de lista, alta y baja. No se
  construye una API desde el portal.

## 5. Plan de implementación (3 ramas chicas e independientes)

Todas salen de `dev`. El orden sugerido es 1 -> 2 -> 3, pero 2 y 3 solo dependen de los
módulos de la 1 (se puede ramificar de la 1 una vez integrada).

### Pedidos al arquitecto (antes o en paralelo; no los toca la persona C)

| Pedido | Por qué |
|---|---|
| Agregar a `NAVEGACION` (y a `PAGINAS_SPA`) Red de mulas, `/administracion/salud`, `/administracion/respaldos`, `/administracion/usuarios`, con su permiso; añadir `inicio` por rol en `GET /api/v1/sesion` y el `nombre` del agente | Hoy la SPA parchea la navegación; la landing por rol y la sesión con nombre son contrato |
| Que una ruta de página desconocida con sesión sirva el `index.html` de la SPA (la SPA muestra "no encontrada") en lugar del JSON 404 | `admin-respaldos-404-d.png` |
| Permiso `RESPALDOS` (y `ADMINISTRAR_USUARIOS` si procede) solo del administrador, en `permisos.py` | `PERMISOS_POR_ROL[ADMINISTRADOR]` ya es `frozenset(Permiso)`: se incluye solo |

### Rama 1: `feature/H-17-ux-modulos-y-navegacion`

Los módulos del catálogo y la navegación por rol, sin cambiar lo que hace cada pantalla.

| Archivos | Cambio |
|---|---|
| `frontend/src/componentes/{MarcoPagina,EncabezadoPagina,TarjetaMetrica,TablaDatos,Insignia,FormularioCampo,AvisoRegion,Permitido,FiltroBarra,VistaVacia,VistaError,MontoColones,FechaCR,MenuPrincipal}.tsx` y `*.test.tsx` | Nuevos |
| `frontend/src/componentes/Marco.tsx` | Usa `MenuPrincipal`; menú móvil colapsable; nombre y rol |
| `frontend/src/componentes/RequiereSesion.tsx`, `paginas/Prohibido.tsx` | Mensaje con el rol que hace falta y enlace a la pantalla de inicio |
| `frontend/src/App.tsx` | `/` redirige a la ruta de inicio del rol |
| `app/estaticos/centinela.css` | Estilos de los módulos y de la cabecera móvil |
| `frontend/src/api/tipos.ts` | Tipos de sesión ampliados (cuando el arquitecto publique) |

### Rama 2: `feature/H-17-ux-pantallas`

Aplicar los módulos y corregir lo que el recorrido encontró.

| Archivos | Cambio |
|---|---|
| `paginas/Detalle.tsx` (+ `BarraDecision`) | Barra de decisión arriba y fija; "Siguiente alerta" tras resolver; cortes de política plegados |
| `paginas/Panel.tsx`, `PanelAlertas.tsx`, `CasoTarjeta.tsx`, `EncabezadoPanel.tsx` | `TablaDatos`, `VistaVacia`, tabla legible en teléfono (tarjetas apiladas bajo 640 px) |
| `paginas/Tablero.tsx`, `tablero/Kpis.tsx`, `Variacion.tsx` | `TarjetaMetrica` con estado `sin_datos`; una sola nota de "sin periodo anterior"; franja de pendientes; selector de periodo con `FiltroBarra` |
| `paginas/Reglas.tsx`, `reglas/Politica.tsx`, `NuevaRegla.tsx`, `ReglaTarjeta.tsx`, `Simulador.tsx` | `FormularioCampo`; fila de política ordenada; textos sin "409" |
| `paginas/Busqueda.tsx`, `RedMulas.tsx`, `Login.tsx` | `FiltroBarra`, `FormularioCampo`; bandeja de propuestas pendientes en Red de mulas si la API ya la permite |
| `app/estaticos/centinela.css`, `detalle.css`, `tablero.css` | Ajustes de espaciado y de teléfono |

### Rama 3: `feature/H-17-administracion`

| Archivos | Cambio |
|---|---|
| `paginas/administracion/{Salud,Respaldos,Usuarios}.tsx` y pruebas | Pantallas nuevas |
| `api/tiposRespaldos.ts` | Tipos del contrato que publique el agente de respaldos |
| `App.tsx` | Rutas `/administracion/*` con `RequiereSesion` |

Depende de que el backend de respaldos esté integrado en `dev`; si no, se construye contra el
doble `apiFalsa.ts` y se cierra al integrar.

### Pruebas e2e de Playwright

| Archivo | Cambio |
|---|---|
| `pruebas/e2e/prueba_spa_login_permisos.py` | Cambiar: cada rol aterriza en su inicio; el menú de cada rol coincide con la tabla 4.1; un agente en `/tablero` ve el mensaje con la salida |
| `pruebas/e2e/prueba_spa_detalle.py` | Cambiar: la barra de decisión es visible sin desplazarse a 1366x900 y a 390x844; "Siguiente alerta" |
| `pruebas/e2e/prueba_spa_panel.py`, `prueba_spa_cola_busqueda.py`, `prueba_spa_casos.py` | Cambiar: selectores de tabla y estados vacíos |
| `pruebas/e2e/prueba_spa_tablero_reglas.py` | Cambiar: estado `sin_datos` del KPI; orden del formulario de política |
| `pruebas/e2e/prueba_spa_admin.py` | **Nueva**: el administrador crea un respaldo y lo verifica; el supervisor recibe acceso denegado en `/administracion/respaldos`; se ve el comando de restauración y no existe un botón Restaurar |
| `pruebas/e2e/prueba_spa_movil.py` | **Nueva**: a 390x844, ninguna pantalla desborda horizontalmente el `<body>` y el menú se abre y cierra |
| Vitest en cada módulo nuevo | Nuevas: `TablaDatos` (cargando, error, vacía, orden), `Permitido`, `MontoColones`, `FechaCR` |

## 6. Lo que no cambiaría

| Qué | Por qué funciona |
|---|---|
| Vista del panel "Por cuenta destino", con sugerencia y lote en la tarjeta | Agrupa por la cuenta mula, que es como se decide; las sugerencias dejan la decisión a la persona (`agente-panel-casos-d.png`) |
| Explicación por regla en el detalle: aporte, acumulado, evidencia y corte alcanzado | Es lo que justifica la alerta ante el cliente y la auditoría |
| Sankey con "marcar mula" y su diálogo de confirmación | Claro, con alternativa en tabla (`agente-red-d.png`) |
| `DialogoConfirmacion` basado en `<dialog>`, foco inicial en Cancelar | Accesible y seguro para acciones que no se deshacen |
| Foco al contenido al cambiar de ruta, regiones `aria-live` en canal, aviso y rezago | Buen punto de partida de accesibilidad |
| Atajos de teclado del panel (`atajos.ts`, `AyudaAtajos`) | Aceleran lo más frecuente |
| Dinero en colones enteros y fechas a hora de Costa Rica al presentar | Cumple el contrato de datos y se ve consistente (`formatos.ts`) |
| Búsqueda forense: filtros combinables, total, CSV con aviso de límite | Cubre el caso de uso sin ruido |
| `RequiereSesion` + guardia del servidor como decisión final | La interfaz solo oculta; quien decide es `permisos.py` |
| Cookie `HttpOnly`/`SameSite=Strict` y redirección segura al volver al login | Segura y sin fricción |

## 7. Estado de la rama 1 (`feature/H-17-ux-por-rol`)

Aprobado por Handel (D-79): el supervisor aterriza en el tablero y Usuarios del portal queda
en solo lectura con el comando de `tareas.py` en v1. La rama 1 incluye este documento, las
capturas, los 14 módulos (sin `BarraDecision`, que es de la rama 2), el menú por rol y
móvil, `Prohibido` con el rol que hace falta, `/` por rol y Salud del sistema. Del servidor:
`NAVEGACION_SPA` y `INICIO_POR_ROL` en `app/api/rutas/spa.py`, `nombre`, `inicio` y `area`
en `GET /api/v1/sesion`, y la página desconocida con sesión que sirve la SPA con estado 404.

Decisión a confirmar: no existía un permiso solo del administrador (`ADMINISTRADOR` tiene
todos, pero ningún permiso es exclusivo suyo), así que `/administracion/*` usa un permiso
nuevo `ADMINISTRACION`. Respaldos traerá el suyo (`RESPALDOS`) en la rama de T-73.
