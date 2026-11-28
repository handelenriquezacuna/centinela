# 09 · Explain de las consultas del panel (H-06)

Planes ganadores de las consultas de la cola de alertas, medidos con
`explain("executionStats")`. Cada consulta usa los filtros de `construir_filtro` y el orden
`ORDEN_PANEL` de `app/repos/alertas.py`, los mismos que usa la API, con limite de pagina 50.

Criterio de H-06:

1. IXSCAN con el indice que le corresponde a la consulta.
2. Sin etapa SORT: el orden sale del indice, no de la memoria.
3. `totalDocsExamined` no mayor que 2 veces `nReturned`. El rango de fechas queda fuera de
   este punto (ver abajo).

## Como se reproduce

```
uv run pytest -q -s pruebas/integracion/prueba_explain_mongo.py -m mongo -rs
```

La prueba crea su propia base (`antifraude_pruebas_explain`) y no lee `antifraude` (T-34).
Le aplica `01-colecciones.js` y `02-indices.js` reales con mongosh, inserta 3000 alertas
sinteticas deterministas, explica las seis consultas y borra la base al final. `-s`
imprime una linea por consulta con indice, SORT, `nReturned` y `totalDocsExamined`.

## Antes: indice `estado, severidad, fecha_creacion` y orden por fecha

Medido sobre 264 alertas producidas por D1 a partir del generador (`--clientes 500
--transacciones 20000`):

| consulta | plan | nReturned | totalDocsExamined |
|---|---|---|---|
| `estado = nueva` | IXSCAN idx_alertas_estado_severidad_fecha + SORT | 50 | 263 |
| `estado = en_revision` | IXSCAN idx_alertas_estado_severidad_fecha + SORT | 0 | 0 |
| `estado = nueva, severidad = CRITICA` | IXSCAN idx_alertas_estado_severidad_fecha + SORT | 50 | 70 |
| `severidad = CRITICA` | COLLSCAN + SORT | 50 | 264 |
| `fecha_creacion` del 1 al 15 de septiembre | COLLSCAN + SORT | 0 | 264 |
| sin filtros | COLLSCAN + SORT | 50 | 264 |

En la clave, `severidad` quedaba en medio y el filtro no la fijaba. Por eso el orden por
fecha no podia salir del indice, y tres de las seis consultas no tenian indice.

Ademas, el panel ordenaba por severidad en Python sobre la pagina ya traida (D-20). Con
mas de una pagina, una alerta CRITICA vieja podia quedar fuera de la primera.

## Cambio (D-20 y H-06)

- **Orden del panel:** `puntaje` desc, `fecha_creacion` desc, `_id` asc. Se ordena por
  puntaje y no por severidad porque la severidad es texto, y su orden alfabetico no
  sirve. Ademas, la severidad sale del puntaje con los cortes crecientes de
  `politica_deteccion`, asi que a mas puntaje toca igual o mayor severidad. El orden en
  Python desaparece.
- **Indices en `scripts/semilla/02-indices.js`:** siguen la regla igualdad, orden, rango.
  Los campos de igualdad del filtro van primero y despues el orden completo del panel.

| indice | clave | sirve a |
|---|---|---|
| idx_alertas_estado_puntaje_fecha | estado, puntaje -1, fecha_creacion -1, _id | cola por estado |
| idx_alertas_estado_severidad_fecha | estado, severidad, puntaje -1, fecha_creacion -1, _id | estado y severidad |
| idx_alertas_severidad_puntaje_fecha | severidad, puntaje -1, fecha_creacion -1, _id | severidad sola |
| idx_alertas_puntaje_fecha | puntaje -1, fecha_creacion -1, _id | sin filtros y rango de fechas |

`idx_alertas_estado_severidad_fecha` conserva el nombre con una clave nueva. El script
borra la version vieja antes de crearla, asi que se puede re-ejecutar sobre una base viva.
Verificado: dos corridas seguidas sobre una base con la clave vieja terminan con la
clave nueva y 7 indices en `alertas`.

## Despues

Medido con la prueba, sobre 3000 alertas sinteticas:

| consulta | plan | SORT | nReturned | totalDocsExamined |
|---|---|---|---|---|
| `estado = nueva` | IXSCAN idx_alertas_estado_puntaje_fecha | no | 50 | 50 |
| `estado = en_revision` | IXSCAN idx_alertas_estado_puntaje_fecha | no | 50 | 50 |
| `estado = nueva, severidad = CRITICA` | IXSCAN idx_alertas_estado_severidad_fecha | no | 50 | 50 |
| `severidad = CRITICA` | IXSCAN idx_alertas_severidad_puntaje_fecha | no | 50 | 50 |
| `fecha_creacion` del 1 al 15 de septiembre | IXSCAN idx_alertas_puntaje_fecha | no | 50 | 300 |
| sin filtros | IXSCAN idx_alertas_puntaje_fecha | no | 50 | 50 |

El rango de fechas es la unica consulta que examina mas documentos de los que devuelve.
Esto es esperado: el rango va despues del orden, asi que Mongo recorre el indice ya
ordenado y descarta las fechas que caen fuera. Aun asi evita el COLLSCAN y el SORT en
memoria. Un indice que empiece por `fecha_creacion` lo serviria mejor, pero no daria el
orden por puntaje. Como el panel abre por estado, no por rango, no se agrega.

Las cifras de antes y despues vienen de volumenes distintos (264 alertas reales contra
3000 sinteticas). La comparacion que importa es el plan: COLLSCAN o SORT contra IXSCAN
sin SORT.

## Casos del panel (T-56)

La vista por caso agrupa las alertas abiertas por `cuenta_destino` con un solo `$group`
(`app/repos/casos_destino.py`). Medido con `pruebas/integracion/prueba_casos_destino_mongo.py`
sobre 604 alertas (una de cada tres cerrada), con el indice real de `02-indices.js`:

| consulta | plan | SORT | nReturned | totalDocsExamined |
|---|---|---|---|---|
| todos los casos (`$match estado in [nueva, en_revision]` + `$group`) | IXSCAN idx_alertas_estado_puntaje_fecha | no | 204 | 204 |
| un caso (`cuenta_destino` + `estado`), el fragmento que pide el SSE | IXSCAN idx_alertas_destino_estado | no | 3 | 3 |

En los dos casos `nReturned == totalDocsExamined`: cada documento leido entra al grupo, y
las 400 alertas cerradas no se tocan. El indice `idx_alertas_destino_estado`
(`{cuenta_destino: 1, estado: 1}`) es nuevo: sin el, pedir el caso de una cuenta recorreria
todas las alertas abiertas. El orden de los casos (severidad maxima, puntaje maximo, mas
antigua) se hace sobre los grupos, no sobre las alertas.

## Cola con las alertas "No fui yo" arriba (T-82)

La respuesta "No fui yo" del cliente sube la alerta al tope de la cola. Se hace en dos tramos, cada uno con su
indice (`app/repos/alertas.py`): las prioritarias leen el indice parcial `idx_alertas_no_fui_yo`
(`{puntaje: -1, fecha_creacion: -1, _id: 1}` con `partialFilterExpression: {respuesta_cliente.valor: "no_fui_yo"}`)
y el resto usa el filtro de siempre mas `$nor` de las prioritarias. Medido con
`pruebas/integracion/prueba_explain_mongo.py`, 3.000 alertas sinteticas, 12 con "No fui yo" (5 abiertas):

| consulta | indice | SORT | nReturned | totalDocsExamined |
|---|---|---|---|---|
| prioritarias, con `hint` | idx_alertas_no_fui_yo | no | 5 | 12 |
| prioritarias, SIN `hint` | idx_alertas_estado_severidad_fecha | SI | 5 | 1.203 |
| resto, sin filtros | idx_alertas_puntaje_fecha | no | 50 | 50 |
| resto, estado `nueva` | idx_alertas_estado_puntaje_fecha | no | 50 | 50 |
| resto, severidad `CRITICA` | idx_alertas_severidad_puntaje_fecha | no | 50 | 50 |

Lo que dice la tabla:

- Sin el `hint` el planificador prefiere el indice por estado y ordena en memoria (1.203 documentos): por eso el
  repositorio lo declara. Si el indice no existe en una base vieja, descarta el hint y la cola sale igual.
- El costo del tramo "resto" es leer, y descartar, las pocas prioritarias que caen dentro del rango recorrido:
  como maximo `nReturned + #prioritarias` documentos. Con muchas respuestas "No fui yo" abiertas creceria, pero
  son las alertas que el agente va a atender primero y se cierran al resolverse.
- El indice parcial solo guarda las alertas con "No fui yo": su costo de escritura es cero para las demas.
- Casos por cuenta destino: la marca (`$set` + `$sum` dentro del `$group` que ya leia todas las abiertas) no agrega
  lecturas; el orden de los casos ya se hacia sobre los grupos, en memoria.
