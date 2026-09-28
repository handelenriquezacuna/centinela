"""Repositorios: el UNICO lugar del sistema con consultas a MongoDB.

La regla la verifica `pruebas/arquitectura/prueba_capas.py`, que recorre el arbol de
`app/` con el modulo `ast` y falla si encuentra una llamada al driver fuera de aqui.

Por que la regla existe: cuando las consultas se dispersan por los routers y los
servicios, nadie puede responder "que consultas hace este sistema" sin leerlo
completo, y optimizar un indice se vuelve una caceria. Con la regla, la respuesta
esta en una sola carpeta.

La lectura recurrente ademas se empuja al servidor (vistas y pipelines
materializados, H-22 y H-24): la API los consume, no los reimplementa.
"""
