"""Respaldos de la base con mongodump y mongorestore (D-74, Unidad 8.6 del curso).

- `manifiesto`: el documento JSON que acompana a cada archivo y la comparacion de conteos
  (puro: sin Mongo, sin procesos).
- `servicio`: crear, verificar y listar. Corre las herramientas como subprocesos asincronos
  y mide con `app/repos/respaldos.py`.

Restaurar sobre la base viva NO esta aqui a proposito: es `python tareas.py restaurar`.
"""
