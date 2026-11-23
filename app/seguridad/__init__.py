"""Seguridad del portal y la API (H-25): contrasenas, tokens de sesion y permisos.

Este paquete no importa FastAPI ni el driver: son funciones puras que se prueban sin
levantar nada. La pegadura con HTTP vive en `app/api/sesion.py`, y la unica consulta a
Mongo (leer y guardar la credencial de un agente) vive en `app/repos/agentes.py`.
"""
