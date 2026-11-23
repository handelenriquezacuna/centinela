"""Doble en memoria de `ProtocoloRepoBusqueda` (H-15).

Filtra en Python lo mismo que el repositorio real delega a Mongo. La prueba marcada
`mongo` compara los dos caminos sobre datos generados.
"""

from __future__ import annotations

import copy
from typing import Any

from app.repos.busqueda import FiltroBusqueda, ResultadoBusqueda


def _cumple(
    documento: dict[str, Any], filtro: FiltroBusqueda, ids: set[str] | None
) -> bool:
    if filtro.desde is not None and documento["fecha"] < filtro.desde:
        return False
    if filtro.hasta is not None and documento["fecha"] > filtro.hasta:
        return False
    if filtro.monto_min is not None and documento["monto_crc"] < filtro.monto_min:
        return False
    if filtro.monto_max is not None and documento["monto_crc"] > filtro.monto_max:
        return False
    if filtro.canales and documento["canal"] not in filtro.canales:
        return False
    if ids is not None and documento["_id"] not in ids:
        return False
    return filtro.cuenta is None or filtro.cuenta in (
        documento["cuenta_origen"],
        documento.get("sinpe_origen"),
        documento["cuenta_destino"],
    )


class RepoBusquedaFalso:
    def __init__(
        self, transacciones: list[dict[str, Any]], alertas: list[dict[str, Any]]
    ) -> None:
        self._transacciones = [copy.deepcopy(t) for t in transacciones]
        self._alertas = [copy.deepcopy(a) for a in alertas]
        self.llamadas: list[dict[str, Any]] = []

    async def buscar(
        self,
        filtro: FiltroBusqueda,
        *,
        limite: int,
        desplazamiento: int = 0,
        depurar: bool = False,
    ) -> ResultadoBusqueda:
        self.llamadas.append(
            {
                "filtro": filtro,
                "limite": limite,
                "desplazamiento": desplazamiento,
                "depurar": depurar,
            }
        )
        ids = (
            {
                a["transaccion_id"]
                for a in self._alertas
                if a["estado"] == filtro.estado_alerta
            }
            if filtro.estado_alerta is not None
            else None
        )
        coinciden = [t for t in self._transacciones if _cumple(t, filtro, ids)]
        # fecha descendente; empate por _id ascendente (dos pasadas estables)
        coinciden.sort(key=lambda t: t["_id"])
        coinciden.sort(key=lambda t: t["fecha"], reverse=True)
        pagina = coinciden[desplazamiento : desplazamiento + limite]
        por_transaccion = {a["transaccion_id"]: a for a in self._alertas}
        for documento in pagina:
            documento.pop("simulacion", None)
            alerta = por_transaccion.get(documento["_id"])
            documento["alerta_id"] = alerta["_id"] if alerta else None
            documento["alerta_estado"] = alerta["estado"] if alerta else None
        plan = {"etapas": ["IXSCAN"], "indices": ["doble"]} if depurar else None
        return ResultadoBusqueda(pagina, len(coinciden), plan)
