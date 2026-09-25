# 07 · Reparto de código — Práctica 1

**Vence 28 de septiembre — quedan 4 días.** Es H-01+H-02 con alcance recortado
(ver `03-historias-usuario.md`): un script único, estilo `HospitalDB.js`, que
crea las 11 colecciones de Centinela con validadores, documentos de ejemplo a
mano, índices con nombre y consultas de comprobación.

## Por qué el código sí se puede dividir sin dependencias, aunque tenga relaciones

A diferencia del Avance 1 (donde bastaba con que cada apartado partiera de un
documento ya escrito), acá una transacción necesita el `_id` de una cuenta que
"exista". La solución no es que todos escriban el mismo archivo a la vez — es
fijar de antemano **qué IDs va a tener cada cosa**, en
[`scripts/practica1/CONTRATO-IDS.md`](../scripts/practica1/CONTRATO-IDS.md).
Con ese contrato, B puede escribir `cuenta_origen: "CTA-001"` en una
transacción sin que el archivo de A exista todavía — el contrato reemplaza al
archivo de la otra persona.

Los datos de ejemplo **no se inventan**: son los del caso de uso UC-02/UC-03/UC-04
ya documentado en `02-casos-de-uso.md` (María Rodríguez, cuenta `8712-4455`,
transferencia de ₡750.000 a las 22:47, agente José Solís, la cadena de mulas
`6033-9001 → 7104-2288 → 8455-1177 → 6001-3344`). Formalizar lo que ya está
diseñado, no crear una historia nueva.

## Reparto — 11 colecciones ÷ 4 personas

| Persona | Archivo | Colecciones | Depende de |
|---|---|---|---|
| **A** | `scripts/practica1/a-identidad.js` | clientes, cuentas, perfiles_comportamiento | Nada — solo el contrato de IDs |
| **B** | `scripts/practica1/b-transaccional.js` | reglas_deteccion, listas_riesgo, transacciones | Nada — usa `CTA-xxx` del contrato, no el archivo de A |
| **C** | `scripts/practica1/c-alertas.js` | agentes, alertas, casos | Nada — usa `TXN-001`/`REG-xxx` del contrato, no el archivo de B |
| **D** | `scripts/practica1/d-cierre.js` | notificaciones, indicadores_diarios | Nada — usa `ALR-001` del contrato, no el archivo de C |

Cada archivo ya tiene la estructura (create Collection con validator,
insertMany, createIndex) y comentarios `TODO` con lo que falta — no es una
hoja en blanco, es rellenar el `$jsonSchema` y los valores reales.

## Cómo trabajar esta semana

- [ ] **Hoy**: los 4 leen `CONTRATO-IDS.md` juntos, 10 minutos, para que quede
      claro y nadie lo reinterprete distinto.
- [ ] **Hoy–viernes**: cada quien completa su archivo. Se puede correr suelto
      contra una base de prueba para validar sintaxis y que el validador no
      rechace los propios documentos de ejemplo:
      `mongosh --file scripts/practica1/a-identidad.js` (y así con cada uno).
- [ ] **Sábado**: **D ensambla** — junta los 4 archivos en
      `scripts/practica1/centinela-practica1.js` (instrucciones al final de
      `d-cierre.js`), agrega el banner de inicio/fin y el conteo final de las
      11 colecciones, estilo `README_HospitalDB.md`.
- [ ] **Antes de entregar**: correr el script **dos veces seguidas** — debe
      dar el mismo resultado las dos veces, porque `dropDatabase()` recrea
      todo desde cero. Confirmar en Compass: 11 colecciones, cada una con
      documentos e índices.

## Checklist de la entrega (recorte real de H-01+H-02+H-06)

- [ ] Las 11 colecciones existen con `$jsonSchema` (rechazan un documento
      malformado — probarlo con uno a propósito, ver que MongoDB lo rechace).
- [ ] Cada colección tiene al menos 1-4 documentos de ejemplo reales (no `{}`).
- [ ] Cada colección tiene al menos un índice con nombre (`idx_...`).
- [ ] El script hace `db.dropDatabase()` al inicio — se puede correr repetido.
- [ ] Consultas de comprobación al final (una por colección, mínimo).
- [ ] Corre con `mongosh --file` sin errores, de punta a punta.

Relacionado: [`scripts/practica1/CONTRATO-IDS.md`](../scripts/practica1/CONTRATO-IDS.md),
[03 · Historias de usuario](03-historias-usuario.md),
[02 · Casos de uso](02-casos-de-uso.md)
