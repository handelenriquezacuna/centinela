# 08 · Calibración de las reglas (T-33)

H-10 mide al motor contra la etiqueta oculta del generador. Con el catálogo de H-07 tal
como nació, el motor atrapaba casi todo el fraude (93,3 %) pero con mucho ruido (precisión
71,8 %). Este documento registra el experimento que ajustó umbrales antes de la defensa:
qué se movió, con qué datos, y por qué el resultado no es un número fabricado a la medida
de una semilla.

**El número que se cita.** Fuera de muestra (4 semillas que la calibración no vio, en los
dos mundos del generador), el F1 medio pasa de 74,4 % a 89,1 % y la exhaustividad agregada
de 93,8 % a 97,1 %, sin ningún conjunto bajo 91,7 %. El 100 % de exhaustividad y 92,3 % de
precisión de la prueba de H-10 se miden sobre la semilla con la que se calibró: es un
resultado dentro de la muestra y no se presenta como la efectividad del motor.

## Método

El script es `scripts/calibracion/calibrar.py` (fuera de `app/`; no toca Mongo):

```
uv run python scripts/calibracion/calibrar.py
```

1. **Mismos datos y mismo contexto que D1.** Genera el conjunto en memoria con
   `app.generador`, arma por transacción el contexto que arma `RepoDeteccion.armar_contexto`
   (destinos previos del cliente, ráfaga del origen, lista de riesgo, perfil; todo hacia
   atrás desde la fecha de la transacción) y puntúa con el evaluador real
   (`app.deteccion.evaluador.evaluar`). Se comprobó contra Mongo que los contextos son
   idénticos y que la línea base reproduce los números de H-10 (56/60 y 22 falsos
   positivos).
2. **Misma definición de H-10.** Una transacción cuenta como detectada si el motor le
   levanta alerta (puntaje >= `umbral_alerta` y con severidad), sin importar su estado
   posterior. Precisión = detectados / (detectados + falsos positivos); exhaustividad =
   detectados / fraudes.
3. **Calibración y validación separadas.** Se calibra con la semilla del YAML (20261207).
   Se valida con otras cuatro (7, 1234, 98765, 20260501) que la búsqueda nunca ve. Cada
   semilla se corre en dos mundos: el de las pruebas de H-10 (`AjustesGenerador(clientes=200,
   transacciones=5000)`, 4 entidades bancarias) y el de `config/centinela.yml` (5
   entidades). Son 2 conjuntos de calibración y 8 de validación.
4. **Objetivo.** Maximizar el F1 en calibración con exhaustividad >= 90 % en cada
   conjunto. A igual F1, gana la configuración que cambia menos parámetros. La validación
   no elige: solo veta. Una candidata gana si mejora el F1 medio de validación y su
   exhaustividad no baja de 90 % en NINGUNO de los 8 conjuntos.
5. **Rejilla pequeña (17.496 combinaciones).** R-01 umbral {8, 10, 12}; R-03 umbral {0, 1};
   R-05 holgura {0, 60, 120} minutos; R-09 umbral {2, 3, 4}, ventana {15, 30, 60} minutos y
   piso {150.000, 300.000, 450.000} CRC; peso de R-07 {30, 40, 50}; peso de R-09
   {20, 30, 40}; `umbral_alerta` {35, 40, 45, 50}. No se tocan los pesos de R-01, R-03 y
   R-05 (35 + 25 + 15 = 75, el caso documentado) ni se enciende R-11.

## Resultado

Por conjunto, antes (catálogo de H-07) y después (ganadora). Det. = fraudes detectados de
60; FP = falsos positivos; P = precisión; E = exhaustividad.

| Conjunto | Antes: det. / FP / P / E / F1 | Después: det. / FP / P / E / F1 |
|---|---|---|
| Calibración 20261207 (pruebas) | 56 / 22 / 71,8 % / 93,3 % / 81,2 % | 60 / 5 / 92,3 % / 100,0 % / 96,0 % |
| Calibración 20261207 (YAML) | 55 / 24 / 69,6 % / 91,7 % / 79,1 % | 56 / 5 / 91,8 % / 93,3 % / 92,6 % |
| Validación 7 (pruebas) | 55 / 28 / 66,3 % / 91,7 % / 76,9 % | 57 / 9 / 86,4 % / 95,0 % / 90,5 % |
| Validación 7 (YAML) | 54 / 29 / 65,1 % / 90,0 % / 75,5 % | 55 / 11 / 83,3 % / 91,7 % / 87,3 % |
| Validación 1234 (pruebas) | 58 / 40 / 59,2 % / 96,7 % / 73,4 % | 60 / 19 / 75,9 % / 100,0 % / 86,3 % |
| Validación 1234 (YAML) | 58 / 36 / 61,7 % / 96,7 % / 75,3 % | 59 / 11 / 84,3 % / 98,3 % / 90,8 % |
| Validación 98765 (pruebas) | 54 / 49 / 52,4 % / 90,0 % / 66,3 % | 59 / 16 / 78,7 % / 98,3 % / 87,4 % |
| Validación 98765 (YAML) | 56 / 44 / 56,0 % / 93,3 % / 70,0 % | 59 / 14 / 80,8 % / 98,3 % / 88,7 % |
| Validación 20260501 (pruebas) | 58 / 35 / 62,4 % / 96,7 % / 75,8 % | 58 / 15 / 79,5 % / 96,7 % / 87,2 % |
| Validación 20260501 (YAML) | 57 / 22 / 72,2 % / 95,0 % / 82,0 % | 59 / 6 / 90,8 % / 98,3 % / 94,4 % |
| **F1 medio, calibración** | **80,1 %** | **94,3 %** |
| **F1 medio, validación** | **74,4 %** | **89,1 %** |

Agregado de los 8 conjuntos de validación: exhaustividad 93,8 % (450 de 480) a 97,1 % (466
de 480); falsos positivos 283 a 101; precisión 61,4 % a 82,2 %.

Medición real, con D1 corriendo contra Mongo (`pruebas/integracion/prueba_efectividad_mongo.py`,
semilla del YAML, 200 clientes, 5.000 transacciones): de 56/60 con 22 falsos positivos
(precisión 71,8 %, exhaustividad 93,3 %) a **60/60 con 5 falsos positivos (precisión 92,3 %,
exhaustividad 100 %)**. Por patrón, `cadena_mulas` pasó de 10 a 13 de 13 eslabones,
`rafaga_fragmentada` de 27 a 28 de 28 y `monto_atipico_destino_nuevo` sigue en 19 de 19. Los
números del script y los de Mongo coinciden.

## Qué se aplicó

En `scripts/semilla/03-catalogos.js`, cada cambio con su comentario:

| Parámetro | Antes | Ahora |
|---|---|---|
| R-01 umbral (veces el promedio) | 10 | 8 |
| R-05 holgura fuera del horario (minutos) | 0 | 120 |
| `umbral_alerta` (y primer corte, MEDIA) | 40 (MEDIA 40-54) | 35 (MEDIA 35-54) |

Los pesos no cambian. TXN-001 sigue dando 75 CRÍTICA (R-01 35 + R-03 25 + R-05 15): va
16,6 veces el promedio y 227 minutos fuera de horario. R-11 sigue inactiva.
`pruebas/unitarias/prueba_calibracion.py` verifica que la semilla dice exactamente lo que el
script declara como ganadora, que el caso documentado se mantiene y que las métricas usan la
definición de H-10. Para aplicar a una base ya sembrada: volver a correr `03-catalogos.js`.

Costo, dicho claro: con `umbral_alerta` 35, R-01 (35 puntos) abre alerta por sí sola. Antes
solo R-07 lo hacía. Es MEDIA, y el supervisor puede volver a 40 por la API sin desplegar
(UC-07); el precio es exhaustividad.

## Por qué se confía en esto (y hasta dónde)

- La ganadora quedó en el puesto 1.513 del ranking por F1 de calibración. Las 1.512
  candidatas por encima tenían un F1 de calibración igual o mayor (hasta 97,8 % contra
  94,3 %) y fueron vetadas en validación. Por ejemplo, `umbral_alerta` 45, R-01 en 8, R-03
  en 1 y R-09 con 2 transferencias en 15 minutos da 100 % de F1 en una semilla y solo 85,0 %
  de exhaustividad en otra. Esa es la forma de sobreajuste que la validación debía atrapar.
  La ganadora es menos brillante en calibración y se sostiene en los ocho conjuntos de
  validación.
- Las tres perillas ganadoras tienen lectura de negocio (monto muy atípico, salirse más de
  dos horas del horario, un solo indicador fuerte basta para revisar); no son decimales
  ajustados.

## Limitaciones

- El generador no es fraude real: los tres patrones son los que se escribieron en
  `app/generador/fraude.py` y las reglas se calibran contra ellos. Que el motor atrape el
  100 % del fraude sintético no dice nada sobre el fraude de un banco; sirve para comparar
  configuraciones entre sí y para la demo, no como cifra de desempeño.
- Con datos reales hay que volver a medir: el margen de R-01 en 8 depende de que lo legítimo
  del generador no pasa de 5 veces el promedio objetivo del cliente.
- Los falsos positivos que quedan (5 en la semilla del YAML; 6 a 19 en las demás) son todos
  la misma combinación: R-03 + R-05 (25 + 15 = 40), una transferencia legítima a un destino
  nuevo hecha más de dos horas fuera del horario habitual. Subir `umbral_alerta` sobre 40 los
  elimina, pero también deja sin alerta eslabones de cadenas de mulas (hasta 78 % de
  exhaustividad en un conjunto de validación): se prefirió no perder fraude.
- Los dobles de prueba de la API (`pruebas/dobles/`) y la copia de
  `pruebas/unitarias/prueba_evaluador.py` conservan la línea base de H-07 a propósito; sus
  encabezados lo dicen. Lo que verifica la semilla real es `prueba_reglas_mongo.py` y
  `prueba_calibracion.py`.
