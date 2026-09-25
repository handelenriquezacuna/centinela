# Contrato de IDs — Práctica 1

Esto es lo que hace posible que las 4 personas escriban su archivo **sin
esperar el de nadie más**: los IDs que cada quien usa para referenciar otra
colección están fijados aquí de antemano, así que B puede escribir una
transacción con `cuenta_origen: "CTA-001"` sin necesitar que el archivo de A
exista todavía — solo necesita este contrato.

Reutiliza el caso de uso ya documentado en `docs/02-casos-de-uso.md` (María
Rodríguez, cuenta `8712-4455`, agente José Solís) — no se inventan datos
nuevos, se formaliza lo que ya está diseñado.

## CLI — clientes

| _id | nombre | cédula (inventada) |
|---|---|---|
| `CLI-001` | María Rodríguez Vargas | 1-1111-1111 |
| `CLI-002` | Luis Fernández Solano | 2-2222-2222 |
| `CLI-003` | Ana Castro Jiménez | 3-3333-3333 |
| `CLI-004` | Carlos Mora Rojas | 4-4444-4444 |

## CTA — cuentas (una por cliente, para mantenerlo simple)

| _id | cuenta_sinpe | cliente_id |
|---|---|---|
| `CTA-001` | `8712-4455` | `CLI-001` (María) |
| `CTA-002` | `6210-3387` | `CLI-002` |
| `CTA-003` | `5544-9021` | `CLI-003` |
| `CTA-004` | `3390-6612` | `CLI-004` |

Las cuentas **mula** (destinos externos, no son clientes nuestros) NO llevan
documento en `cuentas` — son solo el número de cuenta como string dentro de
`transacciones.cuenta_destino` y, si se identifican como sospechosas, un
documento en `listas_riesgo`.

## AGT — agentes

| _id | nombre |
|---|---|
| `AGT-001` | José Solís |
| `AGT-002` | Laura Méndez |

## REG — reglas_deteccion (código corto tal como aparece en los casos de uso)

| _id | codigo | descripcion | peso |
|---|---|---|---|
| `REG-001` | `R-01` | Monto sobre 10× el promedio del cliente | 35 |
| `REG-002` | `R-03` | Destino que el cliente nunca ha usado | 25 |
| `REG-003` | `R-05` | Transacción fuera del horario habitual | 15 |

## RSK — listas_riesgo (la cadena de mulas del caso de uso UC-04)

| _id | cuenta | motivo |
|---|---|---|
| `RSK-001` | `6033-9001` | Primer salto de una cadena de fragmentación |
| `RSK-002` | `7104-2288` | Segundo salto |
| `RSK-003` | `8455-1177` | Tercer salto |
| `RSK-004` | `6001-3344` | Cuarto salto |

## TXN — transacciones (la de UC-02, más 2-3 normales de relleno)

| _id | cuenta_origen | cuenta_destino | monto | hora |
|---|---|---|---|---|
| `TXN-001` | `CTA-001` | `6033-9001` | 750000 | 22:47 (la sospechosa) |
| `TXN-002..TXN-00N` | cualquiera de CTA-002..004 | destino habitual | monto normal | horario normal |

## ALR — alertas

| _id | transaccion_id | reglas_disparadas | puntaje | agente_id | estado |
|---|---|---|---|---|---|
| `ALR-001` | `TXN-001` | `["REG-001","REG-002","REG-003"]` | 75 | `AGT-001` | `confirmada` |

## CAS — casos (escalación de ALR-001, investigando la cadena RSK)

| _id | alertas | estado |
|---|---|---|
| `CAS-001` | `["ALR-001"]` | `investigacion` |

## NOT — notificaciones

| _id | alerta_id | canal |
|---|---|---|
| `NOT-001` | `ALR-001` | correo |

## Perfiles e indicadores (materializados — para Práctica 1 se insertan a mano)

- `perfiles_comportamiento` se indexa por `cliente_id`. Para `CLI-001` usar los
  valores ya documentados: 47 transferencias, promedio ₡45.200, máximo ₡180.000,
  horario habitual 07:00–19:00.
- `indicadores_diarios` se indexa por `fecha` (string `YYYY-MM-DD`).

## Convención general

- Todos los `_id` son **strings legibles** (no ObjectId autogenerado) — más
  fácil de referenciar a mano entre archivos y de leer en la demo.
- Fechas como `ISODate` de Mongo, no strings, salvo donde se indique lo contrario.
