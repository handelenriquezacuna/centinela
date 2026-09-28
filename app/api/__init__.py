"""Capa HTTP: routers, esquemas de entrada y salida, y la forma del error.

Nada de `app/deteccion/` importa este paquete: el motor se construye headless y no
sabe que existe una pantalla. Lo verifica `pruebas/arquitectura/prueba_capas.py`.
"""

PREFIJO_V1 = "/api/v1"
