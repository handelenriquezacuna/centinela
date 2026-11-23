"""El conjunto de demo respeta el tope SINPE Movil de la entidad de cada cuenta de origen.

La regla es la del generador (docs/06-plataforma.md, 7b): ningun monto supera el tope de
la entidad de su cuenta de origen. El demo es literal, asi que se comprueba aparte.
"""

from __future__ import annotations

from app.nucleo.config import Ajustes
from pruebas.dobles.datos_demo import CUENTAS, TRANSACCIONES


def prueba_ningun_monto_del_demo_supera_el_tope_de_su_entidad() -> None:
    topes = {entidad.nombre: entidad.tope_crc for entidad in Ajustes().generador.entidades}
    entidad_de = {cuenta.cuenta_id: cuenta.entidad for cuenta in CUENTAS}

    for transaccion in TRANSACCIONES:
        entidad = entidad_de[transaccion.cuenta_origen]
        assert entidad in topes, f"{transaccion.transaccion_id}: cuenta sin entidad conocida"
        assert transaccion.monto_crc <= topes[entidad], (
            f"{transaccion.transaccion_id}: {transaccion.monto_crc} supera el tope "
            f"{topes[entidad]} de {entidad}"
        )


def prueba_las_cuentas_de_la_cadena_existen_y_su_sinpe_coincide() -> None:
    sinpe_de = {cuenta.cuenta_id: cuenta.cuenta_sinpe for cuenta in CUENTAS}
    for transaccion in TRANSACCIONES:
        assert sinpe_de[transaccion.cuenta_origen] == transaccion.sinpe_origen
