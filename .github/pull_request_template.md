## Historia

H-XX: descripcion corta de la historia

Cierra #

## Cambios

-

## Contratos tocados

Marcar cuales aplican, o dejar solo "ninguno".

- [ ] Dinero (`monto_crc`, entero de 64 bits)
- [ ] Fechas (ISODate UTC)
- [ ] Identificadores (CLI-001, TXN-001)
- [ ] `esquema_version`
- [ ] Estados de alerta (maquina de estados)
- [ ] Colecciones (nombres, indices, validadores)
- [ ] SSE (formato del evento)
- [ ] Forma del error
- [ ] Ninguno

## Pruebas

Comandos y resultado:

- Sin Mongo (`python tareas.py pruebas --sin-mongo`):
- Con Mongo en modo estricto (`CENTINELA_EXIGIR_MONGO=1 uv run pytest -m mongo -rs`):
- Skipped: 0

## Revisor esperado

Segun `docs/07-plan-de-equipo.md`: A, B, C o D.
