"""Lo que los respaldos (D-74) necesitan de la base: contar y borrar. Nada mas.

`mongodump` y `mongorestore` hacen el trabajo pesado fuera del proceso; aqui solo se mide
(documentos por coleccion, para el manifiesto y para la comparacion de la verificacion) y se
borra la base temporal donde se restaura para verificar. Son las unicas consultas del
modulo de respaldos y viven aqui por la regla de capas.

El cliente es el de respaldo (`respaldos.uri`, usuario `centinela_respaldo`) si esta
configurado, y el de la app si no. Se recibe el cliente y no una base porque la base
temporal es OTRA base del mismo servidor.
"""

from __future__ import annotations

from pymongo import AsyncMongoClient

# Solo colecciones reales: las vistas (05-vistas.js) no tienen documentos propios, se
# respaldan como definicion y se vuelven a crear al restaurar.
SOLO_COLECCIONES = {"type": "collection"}


class RepoRespaldos:
    def __init__(self, cliente: AsyncMongoClient) -> None:
        self._cliente = cliente

    async def contar_documentos(self, base: str) -> dict[str, int]:
        """Documentos por coleccion de `base`, ordenadas por nombre. Base ausente: vacio."""
        destino = self._cliente[base]
        nombres = await destino.list_collection_names(filter=SOLO_COLECCIONES)
        conteos: dict[str, int] = {}
        for nombre in sorted(n for n in nombres if not n.startswith("system.")):
            conteos[nombre] = await destino[nombre].count_documents({})
        return conteos

    async def borrar_base(self, base: str) -> None:
        """Borra una base entera. Idempotente. El servicio decide cuales se pueden borrar."""
        await self._cliente.drop_database(base)
