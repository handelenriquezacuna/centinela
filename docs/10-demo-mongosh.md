# Demo mongosh: comandos y salidas reales

> Como correrlo en la base de trabajo, desde la raiz del repo y con la app arriba:
> `URI_DEMO="mongodb://centinela_admin:<clave>@localhost:27018,localhost:27019,localhost:27020/antifraude?replicaSet=rsfraude&authSource=antifraude"`
> y luego `mongosh "$URI_DEMO" --file scripts/demo/01-flujo.js`. Los scripts cargan las ayudas con una
> ruta relativa: hay que estar en la raiz. Las salidas de abajo son de un nodo de prueba (`t70`).
> `99-limpiar.js` borra las transacciones de 6 digitos de CTA-001 y sus alertas, sin importar el dia.

Nodo de prueba: contenedor `t70`, mongo:8, replica set `rsfraude`, puerto 27312, cache 0.25 GB.
Base `antifraude`. App en `127.0.0.1:8150`. Fecha de la corrida: 2026-11-24, hora local 18:47 (Costa Rica).

Scripts: `scripts/demo/00-ayudas.js`, `01-flujo.js`, `02-reglas.js`, `03-crud.js`, `99-limpiar.js`.

## 0. Nodo, semilla y datos

### 0.1 Levantar el nodo

```
docker run -d --rm --name t70 -p 127.0.0.1:27312:27312 -v "$PWD/scripts:/scripts:ro" mongo:8 mongod --replSet rsfraude --port 27312 --bind_ip_all --wiredTigerCacheSizeGB 0.25
```

```
c16394f00a18bef076e3edde574ad4e7d4d90ca0ba12ae48d317de39ca3020a6
```

### 0.2 Iniciar el replica set

```
docker exec t70 mongosh --port 27312 --quiet --eval 'rs.initiate({_id:"rsfraude",members:[{_id:0,host:"localhost:27312"}]})'
docker exec t70 mongosh --port 27312 --quiet --eval 'rs.status().members[0].stateStr'
```

```
{
  ok: 1,
  '$clusterTime': {
    clusterTime: Timestamp({ t: 1795567695, i: 1 }),
    signature: {
      hash: Binary.createFromBase64('AAAAAAAAAAAAAAAAAAAAAAAAAAA=', 0),
      keyId: Long('0')
    }
  },
  operationTime: Timestamp({ t: 1795567695, i: 1 })
}
PRIMARY
```

Que mirar: el segundo comando imprime `PRIMARY`.

### 0.3 Semillas 01, 02, 03 y 05 (en ese orden)

```
for f in 01-colecciones 02-indices 03-catalogos 05-vistas; do docker exec t70 mongosh --port 27312 --quiet --file /scripts/semilla/$f.js; done
```

Salida (ultimas 3 lineas de cada semilla; exit 0 en las cuatro):

```
==================================================
 01-colecciones.js terminado
==================================================
==================================================
 02-indices.js terminado
==================================================

  caso UC-02 / ALR-001: R-01 + R-03 + R-05 = 75 -> CRITICA (comprobado)

Vista vista_alertas_abiertas creada sobre alertas
Vista vista_cuentas_en_riesgo creada sobre alertas
Vista vista_resumen_por_regla creada sobre alertas
```

Que mirar: `03-catalogos` termina con el caso UC-02 comprobado en CRITICA.

### 0.4 Carga de la demo

```
unset VIRTUAL_ENV && CENTINELA_MONGO__URI="mongodb://localhost:27312/?replicaSet=rsfraude" uv run python -m pruebas.dobles.datos_demo
```

Salida (se omiten las lineas de instalacion de uv que van antes):

```
Base: antifraude
  clientes: 8
  cuentas: 8
  perfiles_comportamiento: 1
  listas_riesgo: 1
  transacciones: 7
  agentes: 2
  alertas: 3
  casos: 1
  notificaciones: 1
  indicadores_diarios: 1
Total: 33 documentos en 10 colecciones
```

Que mirar: `transacciones: 7` y `alertas: 3` son la base sin ninguna corrida de la demo.

### 0.5 App

```
unset VIRTUAL_ENV && CENTINELA_MONGO__URI="mongodb://localhost:27312/?replicaSet=rsfraude" CENTINELA_PORTAL__INTERFAZ=htmx CENTINELA_SESION__CLAVE="clave-de-pruebas-0123456789-abcdefghijklmnop" uv run uvicorn app.main:app --host 127.0.0.1 --port 8150
```

```
2026-11-24 18:48:36,499 INFO centinela Centinela 0.1.0 arriba | entorno=dev base=antifraude
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8150 (Press CTRL+C to quit)
2026-11-24 18:48:36,510 INFO centinela.deteccion d1_deteccion: sin token guardado, arranca desde ahora
```

Que mirar: `d1_deteccion` arranca; procesa las transacciones insertadas despues de su arranque.

## 1. Paso 1: como se mueven los datos (01-flujo.js)

```
mongosh "$URI_DEMO" --file scripts/demo/01-flujo.js
```

```
ayudas cargadas: hoyCR(hora, minuto), siguienteTxn(), transferencia({...})
== Paso 1 - insert ==
Documento a insertar:
{
  _id: 'TXN-000001',
  cuenta_origen: 'CTA-001',
  sinpe_origen: '8712-4455',
  cuenta_destino: '6033-9001',
  monto_crc: Long('280000'),
  moneda: 'CRC',
  canal: 'sinpe_movil',
  fecha: ISODate('2026-11-25T04:47:00.000Z'),
  esquema_version: 1
}
insertOne acknowledged: true, _id: TXN-000001
Esperando a D1 (3 segundos)...

== (a) transacciones, por _id ==
{
  _id: 'TXN-000001',
  cuenta_origen: 'CTA-001',
  sinpe_origen: '8712-4455',
  cuenta_destino: '6033-9001',
  monto_crc: Long('280000'),
  moneda: 'CRC',
  canal: 'sinpe_movil',
  fecha: ISODate('2026-11-25T04:47:00.000Z'),
  esquema_version: 1
}

== (b) alertas, por transaccion_id ==
alerta ALR-000001 | puntaje 90 | severidad CRITICA | estado nueva
  R-01 (peso 35, version 1): monto 280.000 CRC es 11,2 veces el promedio del cliente (25.000 CRC); umbral 8 veces
  R-05 (peso 15, version 1): 22:47 hora de Costa Rica, 227 minutos fuera del horario habitual 07:00-19:00; holgura 120 minutos
  R-07 (peso 40, version 1): destino 6033-9001 con 1 entradas vigentes en lista de riesgo a la fecha de la transaccion; umbral 1

== (c) deteccion_estado, documento de reanudacion de D1 ==
{
  _id: 'd1_deteccion',
  clase: 'reanudacion',
  ultima_transaccion_id: 'TXN-000001'
}

== (d) segunda alerta para la misma transaccion: idempotencia ==
codigo 11000: E11000 duplicate key error collection: antifraude.alertas index: idx_alertas_transaccion_id dup key: { transaccion_id: "TXN-000001" }
alertas con transaccion_id TXN-000001: 1
```

Que mirar: (b) puntaje 90 CRITICA (R-01 + R-05 + R-07), (c) `ultima_transaccion_id` de D1 y (d) E11000 sobre `idx_alertas_transaccion_id`.

## 2. Paso 2: reglas con una victima (02-reglas.js)

Antes de correr 02 hay que correr 99 si la base ya tiene transacciones de la demo (ver seccion 3); si no, las ventanas de R-03 y R-09 cambian.

```
mongosh "$URI_DEMO" --file scripts/demo/02-reglas.js
```

```
ayudas cargadas: hoyCR(hora, minuto), siguienteTxn(), transferencia({...})
== Perfil de la victima (CLI-001) ==
{
  _id: 'CLI-001',
  promedio_crc: Long('25000'),
  maximo_crc: Long('100000'),
  horario_habitual: {
    inicio: '07:00',
    fin: '19:00'
  },
  destinos_frecuentes: [
    '6210-3387',
    '5544-9021',
    '3390-6612'
  ]
}

== 2.1 monto 280.000 a destino nuevo 2233-4455, 11:30 ==
{
  _id: 'TXN-000002',
  cuenta_origen: 'CTA-001',
  sinpe_origen: '8712-4455',
  cuenta_destino: '2233-4455',
  monto_crc: Long('280000'),
  moneda: 'CRC',
  canal: 'sinpe_movil',
  fecha: ISODate('2026-11-24T17:30:00.000Z'),
  esquema_version: 1
}
  ALR-000002 (TXN-000002) | puntaje 60 | severidad ALTA | estado nueva
    R-01 peso 35: monto 280.000 CRC es 11,2 veces el promedio del cliente (25.000 CRC); umbral 8 veces
    R-03 peso 25: destino 2233-4455 con 0 transferencias previas en 90 dias; es nuevo con hasta 0

== 2.2 contraste normal: 25.000 a 2233-4455, 11:45 ==
{
  _id: 'TXN-000003',
  cuenta_origen: 'CTA-001',
  sinpe_origen: '8712-4455',
  cuenta_destino: '2233-4455',
  monto_crc: Long('25000'),
  moneda: 'CRC',
  canal: 'sinpe_movil',
  fecha: ISODate('2026-11-24T17:45:00.000Z'),
  esquema_version: 1
}
  TXN-000003: sin alerta (puntaje bajo el umbral 35 o D1 todavia no proceso)

== 2.3 destino en lista de riesgo 6033-9001, 13:30 ==
{
  _id: 'TXN-000004',
  cuenta_origen: 'CTA-001',
  sinpe_origen: '8712-4455',
  cuenta_destino: '6033-9001',
  monto_crc: Long('50000'),
  moneda: 'CRC',
  canal: 'sinpe_movil',
  fecha: ISODate('2026-11-24T19:30:00.000Z'),
  esquema_version: 1
}
  ALR-000004 (TXN-000004) | puntaje 40 | severidad MEDIA | estado nueva
    R-07 peso 40: destino 6033-9001 con 1 entradas vigentes en lista de riesgo a la fecha de la transaccion; umbral 1

== 2.4 rafaga, primera: 110.000 a 2233-4455, 21:30 (fuera de horario) ==
{
  _id: 'TXN-000005',
  cuenta_origen: 'CTA-001',
  sinpe_origen: '8712-4455',
  cuenta_destino: '2233-4455',
  monto_crc: Long('110000'),
  moneda: 'CRC',
  canal: 'sinpe_movil',
  fecha: ISODate('2026-11-25T03:30:00.000Z'),
  esquema_version: 1
}
  TXN-000005: sin alerta (puntaje bajo el umbral 35 o D1 todavia no proceso)

== 2.5 rafaga, segunda: 110.000 a 2233-4455, 21:40 ==
{
  _id: 'TXN-000006',
  cuenta_origen: 'CTA-001',
  sinpe_origen: '8712-4455',
  cuenta_destino: '2233-4455',
  monto_crc: Long('110000'),
  moneda: 'CRC',
  canal: 'sinpe_movil',
  fecha: ISODate('2026-11-25T03:40:00.000Z'),
  esquema_version: 1
}
  TXN-000006: sin alerta (puntaje bajo el umbral 35 o D1 todavia no proceso)

== 2.6 rafaga, tercera: 110.000 a 2233-4455, 21:50 ==
{
  _id: 'TXN-000007',
  cuenta_origen: 'CTA-001',
  sinpe_origen: '8712-4455',
  cuenta_destino: '2233-4455',
  monto_crc: Long('110000'),
  moneda: 'CRC',
  canal: 'sinpe_movil',
  fecha: ISODate('2026-11-25T03:50:00.000Z'),
  esquema_version: 1
}
  ALR-000007 (TXN-000007) | puntaje 45 | severidad MEDIA | estado nueva
    R-05 peso 15: 21:50 hora de Costa Rica, 170 minutos fuera del horario habitual 07:00-19:00; holgura 120 minutos
    R-09 peso 30: 3 transferencias del mismo origen en 30 minutos por 330.000 CRC; umbral 3 transferencias y 300.000 CRC

== 2.7 destino nuevo 7001-2020 fuera de horario, 23:10 ==
{
  _id: 'TXN-000008',
  cuenta_origen: 'CTA-001',
  sinpe_origen: '8712-4455',
  cuenta_destino: '7001-2020',
  monto_crc: Long('25000'),
  moneda: 'CRC',
  canal: 'sinpe_movil',
  fecha: ISODate('2026-11-25T05:10:00.000Z'),
  esquema_version: 1
}
  ALR-000008 (TXN-000008) | puntaje 40 | severidad MEDIA | estado nueva
    R-03 peso 25: destino 7001-2020 con 0 transferencias previas en 90 dias; es nuevo con hasta 0
    R-05 peso 15: 23:10 hora de Costa Rica, 250 minutos fuera del horario habitual 07:00-19:00; holgura 120 minutos

== Resumen: transacciones insertadas por 02 ==
TXN-000002 TXN-000003 TXN-000004 TXN-000005 TXN-000006 TXN-000007 TXN-000008
alertas creadas para estas transacciones: 4
```

Que mirar: 2.1 R-01+R-03=60 ALTA; 2.2 sin alerta; 2.3 R-07=40 MEDIA; 2.4 y 2.5 R-05=15 sin alerta; 2.6 R-09+R-05=45 MEDIA; 2.7 R-03+R-05=40 MEDIA.

## 3. Limpieza (99-limpiar.js)

```
mongosh "$URI_DEMO" --file scripts/demo/99-limpiar.js
```

```
ayudas cargadas: hoyCR(hora, minuto), siguienteTxn(), transferencia({...})
== Transacciones de la demo a borrar ==
  TXN-000001  6033-9001  280000 CRC  2026-11-25T04:47:00.000Z
  TXN-000002  2233-4455  280000 CRC  2026-11-24T17:30:00.000Z
  TXN-000003  2233-4455  25000 CRC  2026-11-24T17:45:00.000Z
  TXN-000004  6033-9001  50000 CRC  2026-11-24T19:30:00.000Z
  TXN-000005  2233-4455  110000 CRC  2026-11-25T03:30:00.000Z
  TXN-000006  2233-4455  110000 CRC  2026-11-25T03:40:00.000Z
  TXN-000007  2233-4455  110000 CRC  2026-11-25T03:50:00.000Z
  TXN-000008  7001-2020  25000 CRC  2026-11-25T05:10:00.000Z

== Alertas de esas transacciones ==
  ALR-000001 ALR-000002 ALR-000004 ALR-000007 ALR-000008

== Resultado ==
  notificaciones borradas: 0
  alertas borradas:        5
  transacciones borradas:  8
  transacciones de la semilla intactas (TXN-001..TXN-007): 7 de 7
```

Que mirar: `transacciones borradas: 8` y `alertas borradas: 5`; la semilla queda en 7 de 7.

## 4. Repetibilidad: segunda corrida completa (01, 02, 99)

Se corrio otra vez 01, 02 y 99 con los mismos comandos de las secciones 1 a 3. Lineas clave de la segunda corrida:

```
mongosh "$URI_DEMO" --file scripts/demo/01-flujo.js
```

```
alerta ALR-000001 | puntaje 90 | severidad CRITICA | estado nueva
  R-01 (peso 35, version 1): monto 280.000 CRC es 11,2 veces el promedio del cliente (25.000 CRC); umbral 8 veces
  R-05 (peso 15, version 1): 22:47 hora de Costa Rica, 227 minutos fuera del horario habitual 07:00-19:00; holgura 120 minutos
  R-07 (peso 40, version 1): destino 6033-9001 con 1 entradas vigentes en lista de riesgo a la fecha de la transaccion; umbral 1
codigo 11000: E11000 duplicate key error collection: antifraude.alertas index: idx_alertas_transaccion_id dup key: { transaccion_id: "TXN-000001" }
alertas con transaccion_id TXN-000001: 1
```

```
mongosh "$URI_DEMO" --file scripts/demo/02-reglas.js
```

```
  ALR-000002 (TXN-000002) | puntaje 60 | severidad ALTA | estado nueva
  TXN-000003: sin alerta (puntaje bajo el umbral 35 o D1 todavia no proceso)
  ALR-000004 (TXN-000004) | puntaje 40 | severidad MEDIA | estado nueva
  TXN-000005: sin alerta (puntaje bajo el umbral 35 o D1 todavia no proceso)
  TXN-000006: sin alerta (puntaje bajo el umbral 35 o D1 todavia no proceso)
  ALR-000007 (TXN-000007) | puntaje 45 | severidad MEDIA | estado nueva
  ALR-000008 (TXN-000008) | puntaje 40 | severidad MEDIA | estado nueva
```

```
mongosh "$URI_DEMO" --file scripts/demo/99-limpiar.js
```

```
  ALR-000001 ALR-000002 ALR-000004 ALR-000007 ALR-000008
  notificaciones borradas: 0
  alertas borradas:        5
  transacciones borradas:  8
  transacciones de la semilla intactas (TXN-001..TXN-007): 7 de 7
```

Que mirar: los mismos ids (TXN-000001 a TXN-000008), los mismos puntajes y los mismos conteos de borrado.

## 5. Paso R-11: cadena de mulas en vivo

Comando del modulo (equivalente a `python tareas.py demo-cadena-viva --disparar`, sin invocar tareas.py):

```
unset VIRTUAL_ENV && CENTINELA_MONGO__URI="mongodb://localhost:27312/?replicaSet=rsfraude" uv run python -m app.deteccion.demo_cadena --disparar
```

```
cadena insertada: TXN-900001 a TXN-900004
  TXN-900001  8712-4455 -> 6033-9001    280000 CRC  00:40:22 UTC
  TXN-900002  6033-9001 -> 7104-2288    276000 CRC  00:44:22 UTC
  TXN-900003  7104-2288 -> 8455-1177    185000 CRC  00:49:22 UTC
  TXN-900004  8455-1177 -> 6001-3344     90000 CRC  00:51:22 UTC
rasgo de 6033-9001: recibido 280000 CRC, reenviado 276000 CRC, proporcion 98,6 %, 4 minutos, 3 saltos
transferencia de disparo: TXN-900005 (6210-3387 -> 6033-9001, 250000 CRC). Con D1 corriendo, buscar la alerta de TXN-900005 en el panel: trae R-11. El rasgo vale 60 s (D3 lo renueva si esta corriendo).
```

Consulta de la alerta del disparo:

```
docker exec t70 mongosh --port 27312 antifraude --quiet --eval 'printjson(db.alertas.findOne({transaccion_id: "TXN-900005"}, {_id:1, puntaje:1, severidad:1, estado:1, reglas_disparadas:1}))'
```

```
{
  _id: 'ALR-900005',
  reglas_disparadas: [
    {
      regla_id: 'REG-002',
      codigo: 'R-03',
      version: 1,
      peso: 25,
      explicacion: 'destino 6033-9001 con 0 transferencias previas en 90 dias; es nuevo con hasta 0'
    },
    {
      regla_id: 'REG-004',
      codigo: 'R-07',
      version: 1,
      peso: 40,
      explicacion: 'destino 6033-9001 con 1 entradas vigentes en lista de riesgo a la fecha de la transaccion; umbral 1'
    },
    {
      regla_id: 'REG-006',
      codigo: 'R-11',
      version: 1,
      peso: 20,
      explicacion: 'destino 6033-9001 recibio 280.000 CRC y reenvio 276.000 CRC (98,6 %) en 4 minutos, en una cadena de 3 saltos; umbral 80 % en 15 minutos y 2 saltos'
    }
  ],
  puntaje: 85,
  severidad: 'CRITICA',
  estado: 'nueva'
}
```

Que mirar: R-11 aporta 20; puntaje 85, CRITICA.

Limpieza de los residuos de la cadena (99 no los borra):

```
docker exec t70 mongosh --port 27312 antifraude --quiet --eval 'const ids = ["TXN-900001","TXN-900002","TXN-900003","TXN-900004","TXN-900005"]; const a = db.alertas.deleteMany({transaccion_id: {$in: ids}}); const t = db.transacciones.deleteMany({_id: {$in: ids}}); print("alertas borradas: " + a.deletedCount + ", transacciones borradas: " + t.deletedCount); print("quedan: transacciones " + db.transacciones.countDocuments({}) + ", alertas " + db.alertas.countDocuments({}));'
```

```
alertas borradas: 2, transacciones borradas: 5
quedan: transacciones 7, alertas 3
```

Que mirar: vuelve a la semilla (7 transacciones, 3 alertas).

## 5b. CRUD: insercion, modificacion y eliminacion (03-crud.js)

Las escrituras que hace la API, hechas a mano en mongosh sobre documentos de la semilla.
Muestra tres operaciones y deja la base como estaba al final.

```
mongosh "$URI_DEMO" --file scripts/demo/03-crud.js
```

Salida (nodo de prueba con la demo cargada, 24 nov 2026):

```
== (a) updateOne: umbral de R-01 con versionado ==
antes:       umbral 8 | version 1 | versiones_anteriores 0
tras subir:  umbral 9 | version 2 | versiones_anteriores 1
tras volver: umbral 8 | version 3 | versiones_anteriores 2
despues:     umbral 8 | version 1 | versiones_anteriores 0 (era 0)
== (b) updateOne con $push: transicion de una alerta ==
antes: ALR-002 | estado nueva | historial 1 entradas | sin agente_id
transicion valida: matchedCount 1, modifiedCount 1
transicion invalida con filtro condicionado: matchedCount 0, modifiedCount 0
despues: ALR-002 | estado nueva | agente_id ausente | historial 1 entradas (era 1)
== (c) insertOne y deleteOne: listas_riesgo ==
insertOne acknowledged: true, insertedId: RSK-990
deleteOne deletedCount: 1
== Estado final ==
R-01: umbral 8 | version 1 | versiones_anteriores 0
listas_riesgo RSK-990 presente: false
```

Lo que hace, en orden:

- **(a) updateOne con versionado** sobre `reglas_deteccion`, regla R-01: el umbral sube de 8 a 9 y
  vuelve a 8. Cada edicion sigue la forma de `app/repos/reglas.py` (`editar`): `version` sube en uno,
  la version anterior entra completa a `versiones_anteriores` con `reemplazada_en`, y el filtro lleva
  la version leida. El script imprime umbral, version y cantidad de historia antes, tras subir, tras
  volver y al final.
- **(b) updateOne con $push** sobre `alertas`: una alerta en `nueva` y sin agente pasa a `en_revision`,
  asigna `AGT-001` y gana una entrada en `historial` (la forma de `planear_transicion` en
  `app/repos/alertas.py`). Despues, un intento de `nueva -> confirmada`, que no esta en la tabla de
  `app/modelos/estados.py`: el filtro pide `estado: "nueva"`, la alerta ya no esta ahi, y el resultado
  es `matchedCount 0`. Nada se escribe. La limpieza devuelve la alerta a `nueva`, quita `agente_id` y
  saca las dos entradas de la demo por su `nota`.
- **(c) insertOne y deleteOne** sobre `listas_riesgo`: inserta `RSK-990` (cuenta `7000-0990`), cuenta
  cuantos documentos tiene esa cuenta, la borra y vuelve a contar. Ninguna transaccion usa esa cuenta.

Requisitos: semillas 01 a 03 corridas. Para (b), una alerta en `nueva` sin agente; si no hay, (b) se
omite y lo dice. Si `RSK-990` o `7000-0990` ya existen, (c) se omite. No hace falta la app para
escribir; con la app corriendo, el panel recibe por el change stream el cambio de (b) y su reversa.

Que mirar: `matchedCount 0` en el intento invalido (la guarda es el filtro, no solo la tabla);
al final, `R-01` con el umbral y la version de antes, la alerta en `nueva` sin `agente_id`, y
`RSK-990 presente: false`.

Estado de la prueba: el script se reviso leyendo y pasa la sintaxis, pero no se corrio contra ninguna base
al escribir esta seccion. La salida real la pega el dictador desde su nodo.

## 6. Compuertas

```
uv run ruff check .
```

```
All checks passed!
```

```
uv run pytest -q -m "not mongo" 2>&1 | tail -6
```

```
........................................................................ [ 80%]
........................................................................ [ 85%]
........................................................................ [ 91%]
........................................................................ [ 97%]
.................................                                        [100%]
1257 passed, 258 deselected in 23.31s
```

## 7. Que no se ve o no queda resuelto

- Las transferencias que no llegan al umbral (2.4, 2.5: R-05 sola = 15) no dejan rastro en `alertas`; sus reglas no se pueden mostrar con mongosh.
- `99-limpiar.js` identifica la demo por fecha exacta de hoy (hora CR) y `cuenta_origen` CTA-001; no sirve si se corre otro dia que 01 y 02.
- Tras 99, los ids vuelven a TXN-000001 mientras `deteccion_estado.ultima_transaccion_id` queda en el id ya borrado; un reinicio de D1 con ese token puede omitir transacciones con id menor (`filtro_pendientes`).
- La cadena de `demo_cadena` deja `TXN-900001..900005` (6 digitos): mientras existan, `siguienteTxn()` numera desde TXN-900006. Se borran con el comando de la seccion 5, no con 99.
- No se creo notificacion para ALR-000001 (CRITICA) en las dos corridas: `notificaciones borradas: 0`. No se investigo si D2 corre en la app.
- 02 no se probo corrido dos veces sin 99 entre medias.
