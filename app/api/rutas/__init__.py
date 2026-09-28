"""Routers de la API. `enrutador_v1` es lo unico que monta `app/main.py`."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import PREFIJO_V1
from app.api.rutas import alertas

enrutador_v1 = APIRouter(prefix=PREFIJO_V1)
enrutador_v1.include_router(alertas.enrutador)

__all__ = ["enrutador_v1"]
