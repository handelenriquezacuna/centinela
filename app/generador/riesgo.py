"""Lista de riesgo: una parte de las mulas de las cadenas ya esta reportada.

Un banco no conoce todas sus cuentas mula: solo algunas fueron senaladas antes. Aqui se
reporta una fraccion (`proporcion_mulas_reportadas`) de las cuentas mula que RECIBEN en una
cadena, con `vigente: true` y una `fecha_reporte` anterior a la primera transaccion que
reciben (R-07 compara la fecha del reporte contra la de la transaccion). Los ids son de
cuatro digitos (`RSK-0001`) para no chocar con los de tres del catalogo.
"""

from __future__ import annotations

import random
from datetime import timedelta

from app.generador.fraude import CADENA
from app.generador.poblacion import Poblacion
from app.modelos.base import ESQUEMA_VERSION

ANTELACION = timedelta(days=1)


def documentos_lista_riesgo(
    poblacion: Poblacion,
    transacciones: list[dict],
    proporcion: float,
    rng: random.Random,
) -> list[dict]:
    mulas = set(poblacion.cuentas_mula)
    sinpe_de_mula = {
        cuenta.cuenta_sinpe
        for cuenta in poblacion.cuentas
        if cuenta.cuenta_id in mulas
    }
    primera_recepcion: dict[str, object] = {}
    for txn in transacciones:  # cronologico: la primera que aparece es la primera recibida
        if txn["simulacion"].get("patron") == CADENA and txn["cuenta_destino"] in sinpe_de_mula:
            primera_recepcion.setdefault(txn["cuenta_destino"], txn["fecha"])

    candidatas = sorted(primera_recepcion)
    cantidad = round(len(candidatas) * proporcion)
    elegidas = sorted(rng.sample(candidatas, cantidad))
    return [
        {
            "_id": f"RSK-{numero:04d}",
            "cuenta": cuenta,
            "motivo": "Cuenta mula de una cadena de fragmentacion (simulacion)",
            "fecha_reporte": primera_recepcion[cuenta] - ANTELACION,
            "vigente": True,
            "esquema_version": ESQUEMA_VERSION,
        }
        for numero, cuenta in enumerate(elegidas, start=1)
    ]
