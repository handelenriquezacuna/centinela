"""Generador de datos sinteticos de Centinela (H-03, H-04, H-05).

Es un CLI aparte (`python -m app.generador`) y nunca corre dentro del proceso web.
Todo lo que se genera sale de una semilla y de `AjustesGenerador` (bloque `generador`
de `config/centinela.yml`): misma semilla, mismos datos, byte por byte.

Reparto del paquete:

- `poblacion.py`: clientes, cuentas (con entidad y tope SINPE) y perfiles.
- `transacciones.py`: flujo legitimo por hora del dia y ensamblado final.
- `fraude.py`: los tres patrones de vishing y la etiqueta oculta.
- `ejecucion.py`: arma el conjunto y lo escribe por lotes a ritmo configurable.

Las escrituras viven en `app/repos/generador.py`: ninguna consulta fuera de `app/repos/`.
"""
