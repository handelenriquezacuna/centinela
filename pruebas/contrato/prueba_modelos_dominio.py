"""Contrato de los 11 modelos de dominio.

Cada prueba de aqui sostiene una linea de la tabla de contratos de
docs/06-plataforma.md. Si una de estas falla, el contrato cambio.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.modelos import (
    COLECCIONES,
    ESQUEMA_VERSION,
    Alerta,
    Cliente,
    IndicadorDiario,
    PerfilComportamiento,
    Severidad,
    Transaccion,
)
from app.modelos.base import MAXIMO_ENTERO_64
from pruebas.dobles.datos_demo import ZONA_COSTA_RICA

AHORA = datetime(2026, 9, 24, 22, 47, tzinfo=ZONA_COSTA_RICA)


def alerta(**cambios: object) -> Alerta:
    """Alerta valida minima. Incluye los campos de despliegue y la entrada de
    creacion del historial, que el contrato exige siempre."""
    base = {
        "_id": "ALR-001",
        "esquema_version": 1,
        "transaccion_id": "TXN-001",
        "reglas_disparadas": ["REG-001"],
        "puntaje": 75,
        "severidad": Severidad.CRITICA,
        "fecha_creacion": AHORA,
        "cuenta_origen": "8712-4455",
        "cuenta_destino": "6033-9001",
        "monto_crc": 750_000,
        "cliente_nombre": "Maria Rodriguez Vargas",
        "historial": [
            {"accion": "nueva", "fecha": AHORA, "nota": "creada por el motor"}
        ],
    }
    return Alerta(**{**base, **cambios})


def transaccion(**cambios: object) -> Transaccion:
    base = {
        "_id": "TXN-001",
        "cuenta_origen": "CTA-001",
        "cuenta_destino": "6033-9001",
        "monto_crc": 750_000,
        "fecha": AHORA,
    }
    return Transaccion(**{**base, **cambios})


# ---------------------------------------------------------------------------
# Son 11, no 10 ni 12
# ---------------------------------------------------------------------------


def prueba_hay_exactamente_once_colecciones_de_dominio() -> None:
    assert len(COLECCIONES) == 11, "las colecciones de dominio son 11"
    assert set(COLECCIONES) == {
        "clientes",
        "cuentas",
        "perfiles_comportamiento",
        "transacciones",
        "reglas_deteccion",
        "listas_riesgo",
        "agentes",
        "alertas",
        "casos",
        "notificaciones",
        "indicadores_diarios",
    }


# ---------------------------------------------------------------------------
# Dinero
# ---------------------------------------------------------------------------


def prueba_el_dinero_acepta_entero() -> None:
    assert transaccion(monto_crc=750_000).monto_crc == 750_000


@pytest.mark.parametrize("valor", [750_000.0, 750_000.5, "750000"])
def prueba_el_dinero_rechaza_lo_que_no_es_entero(valor: object) -> None:
    """Ni flotante redondo, ni flotante con decimales, ni texto.

    El caso peligroso es `750000.0`: sin `strict=True` Pydantic lo convierte a int sin
    quejarse y un flotante entra al campo de dinero sin que nadie lo note.
    """
    with pytest.raises(ValidationError):
        transaccion(monto_crc=valor)


def prueba_el_dinero_no_es_negativo() -> None:
    with pytest.raises(ValidationError):
        transaccion(monto_crc=-1)


def prueba_el_dinero_llega_hasta_64_bits_y_no_mas() -> None:
    assert transaccion(monto_crc=MAXIMO_ENTERO_64).monto_crc == MAXIMO_ENTERO_64
    with pytest.raises(ValidationError):
        transaccion(monto_crc=MAXIMO_ENTERO_64 + 1)


def prueba_la_moneda_es_crc_desde_el_dia_uno() -> None:
    assert transaccion().moneda == "CRC"
    with pytest.raises(ValidationError):
        transaccion(moneda="USD")


def prueba_el_dinero_del_perfil_tambien_es_entero() -> None:
    """Un promedio es un estadistico, pero sigue siendo dinero: entero de colones."""
    with pytest.raises(ValidationError):
        PerfilComportamiento(
            _id="CLI-001",
            promedio_crc=45_200.75,
            maximo_crc=180_000,
            num_transferencias=47,
            horario_habitual={"inicio": "07:00", "fin": "19:00"},
        )


# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------


def prueba_la_fecha_sin_zona_se_rechaza() -> None:
    with pytest.raises(ValidationError) as fallo:
        transaccion(fecha=datetime(2026, 9, 24, 22, 47))  # noqa: DTZ001 - es lo que se rechaza

    assert "zona horaria" in str(fallo.value)


def prueba_la_fecha_se_normaliza_a_utc() -> None:
    """22:47 en Costa Rica son 04:47 UTC del dia siguiente."""
    guardada = transaccion(fecha=AHORA).fecha

    assert guardada.tzinfo is not None
    assert guardada.utcoffset() == timedelta(0)
    assert (guardada.day, guardada.hour, guardada.minute) == (25, 4, 47)


def prueba_una_fecha_ya_en_utc_no_se_mueve() -> None:
    en_utc = datetime(2026, 9, 25, 4, 47, tzinfo=UTC)
    assert transaccion(fecha=en_utc).fecha == en_utc


def prueba_la_fecha_de_un_documento_embebido_tambien_se_normaliza() -> None:
    """La regla tiene que alcanzar al historial embebido de la alerta."""
    creada = alerta(
        historial=[
            {
                "accion": "en_revision",
                "agente_id": "AGT-001",
                "fecha": AHORA,
                "nota": "toma la alerta",
            }
        ]
    )

    assert creada.historial[0].fecha.utcoffset() == timedelta(0)


def prueba_el_historial_embebido_no_acepta_fecha_sin_zona() -> None:
    with pytest.raises(ValidationError):
        alerta(
            historial=[
                {
                    "accion": "en_revision",
                    "agente_id": "AGT-001",
                    "fecha": datetime(2026, 9, 24, 22, 49),  # noqa: DTZ001 - es lo que se rechaza
                }
            ]
        )


def prueba_la_unica_excepcion_es_la_fecha_de_indicadores() -> None:
    """`indicadores_diarios.fecha` es texto YYYY-MM-DD y es su propio `_id`."""
    indicador = IndicadorDiario(_id="2026-09-24", total_alertas=3)

    assert indicador.fecha == "2026-09-24"
    assert indicador.a_documento()["_id"] == "2026-09-24"

    with pytest.raises(ValidationError):
        IndicadorDiario(_id="24/09/2026", total_alertas=3)
    with pytest.raises(ValidationError):
        IndicadorDiario(_id=datetime(2026, 9, 24, tzinfo=UTC), total_alertas=3)


# ---------------------------------------------------------------------------
# Identificadores
# ---------------------------------------------------------------------------


def prueba_el_id_es_cadena_legible_y_no_objectid() -> None:
    with pytest.raises(ValidationError):
        transaccion(_id="507f1f77bcf86cd799439011")


def prueba_el_id_exige_el_prefijo_de_su_coleccion() -> None:
    """Un `CLI-001` en `transacciones` es un error de referencia, no un detalle."""
    with pytest.raises(ValidationError):
        transaccion(_id="CLI-001")


def prueba_las_referencias_tambien_estan_tipadas() -> None:
    with pytest.raises(ValidationError):
        transaccion(cuenta_origen="CLI-001")


def prueba_el_numero_de_cuenta_sinpe_tiene_formato() -> None:
    with pytest.raises(ValidationError):
        transaccion(cuenta_destino="60339001")


def prueba_el_id_sale_como_guion_bajo_id_al_serializar() -> None:
    """El documento en Mongo manda: el campo se llama `_id`, no `transaccion_id`."""
    documento = transaccion().a_documento()

    assert documento["_id"] == "TXN-001"
    assert "transaccion_id" not in documento


def prueba_se_puede_construir_con_el_nombre_o_con_el_alias() -> None:
    por_alias = Transaccion(
        _id="TXN-001",
        cuenta_origen="CTA-001",
        cuenta_destino="6033-9001",
        monto_crc=1,
        fecha=AHORA,
    )
    por_nombre = Transaccion(
        transaccion_id="TXN-001",
        cuenta_origen="CTA-001",
        cuenta_destino="6033-9001",
        monto_crc=1,
        fecha=AHORA,
    )
    assert por_alias == por_nombre


# ---------------------------------------------------------------------------
# Version de esquema y nombres
# ---------------------------------------------------------------------------


def prueba_todo_documento_lleva_esquema_version() -> None:
    for nombre, modelo in COLECCIONES.items():
        assert "esquema_version" in modelo.model_fields, (
            f"{nombre} tiene que llevar esquema_version"
        )
    assert transaccion().a_documento()["esquema_version"] == ESQUEMA_VERSION


def prueba_ningun_campo_se_llama_id_type_o_class() -> None:
    prohibidos = {"id", "type", "class"}
    for nombre, modelo in COLECCIONES.items():
        for campo, definicion in modelo.model_fields.items():
            assert campo not in prohibidos, f"{nombre}.{campo} es un nombre prohibido"
            alias = definicion.alias
            assert alias not in prohibidos, f"{nombre} usa el alias prohibido {alias}"


def prueba_los_nombres_de_campo_son_snake_case() -> None:
    import re

    patron = re.compile(r"^[a-z][a-z0-9_]*$")
    for nombre, modelo in COLECCIONES.items():
        for campo in modelo.model_fields:
            assert patron.match(campo), f"{nombre}.{campo} no es snake_case"


def prueba_se_lee_un_documento_con_campos_desconocidos() -> None:
    """Compatibilidad hacia adelante: es lo que hace posible la migracion de H-26.

    H-05 agrega a `transacciones` una etiqueta oculta solo para medir. Con
    `extra="forbid"` esa etiqueta romperia toda lectura.
    """
    leida = Transaccion.model_validate(
        {
            "_id": "TXN-001",
            "cuenta_origen": "CTA-001",
            "cuenta_destino": "6033-9001",
            "monto_crc": 750_000,
            "fecha": AHORA,
            "es_fraude_etiqueta_oculta": True,
            "campo_de_una_version_futura": {"lo": "que sea"},
        }
    )

    assert leida.monto_crc == 750_000


# ---------------------------------------------------------------------------
# La alerta
# ---------------------------------------------------------------------------


def prueba_la_alerta_nace_nueva_y_sin_agente() -> None:
    nacida = alerta()

    assert nacida.estado == "nueva"
    assert nacida.agente_id is None


def prueba_la_alerta_exige_historial_desde_que_nace() -> None:
    """La primera entrada es la creacion, y la escribe el motor sin agente.

    Una alerta con historial vacio no podria decir cuando nacio, que es lo primero
    que el rastro de auditoria tiene que responder.
    """
    with pytest.raises(ValidationError):
        alerta(historial=[])

    primera = alerta().historial[0]
    assert primera.accion == "nueva"
    assert primera.agente_id is None, "la creacion no la hizo una persona"


def prueba_la_alerta_copia_los_campos_de_despliegue() -> None:
    """La fila del panel se pinta sin un solo $lookup.

    Los campos van copiados al crear la alerta: "nada se recalcula dentro de una
    peticion HTTP", y el monto que se vio al crearla no puede depender de que la
    transaccion siga igual dentro de un ano.
    """
    documento = alerta().a_documento()

    for campo in ("cuenta_origen", "cuenta_destino", "monto_crc", "moneda", "cliente_nombre"):
        assert campo in documento, f"la alerta tiene que copiar {campo}"

    # En `alertas` la cuenta es el numero SINPE, no el identificador CTA-001: es un
    # campo para mostrar, y el agente lee el numero.
    assert documento["cuenta_origen"] == "8712-4455"
    with pytest.raises(ValidationError):
        alerta(cuenta_origen="CTA-001")


def prueba_la_alerta_exige_al_menos_una_regla() -> None:
    """Una alerta sin motivo no se puede explicar, y explicarse es su razon de ser."""
    with pytest.raises(ValidationError):
        alerta(reglas_disparadas=[])


EMBEBIDA = {
    "regla_id": "REG-001",
    "codigo": "R-01",
    "version": 1,
    "peso": 35,
    "explicacion": "monto 11,2 veces el promedio; umbral 10 veces",
}
POLITICA = {"politica_id": "POL-001", "version": 1}


def prueba_esquema_1_son_referencias() -> None:
    """v1 (antes de H-26): lista de identificadores, sin politica."""
    creada = alerta(reglas_disparadas=["REG-001", "REG-002", "REG-003"])

    assert creada.esquema_version == 1
    assert creada.a_documento()["reglas_disparadas"] == ["REG-001", "REG-002", "REG-003"]
    assert "politica" not in creada.a_documento()
    assert creada.reglas_aplicadas == []


def prueba_esquema_2_es_copia_embebida_con_politica() -> None:
    """v2 (H-26): cada regla con su version, peso y explicacion, y la politica usada."""
    creada = alerta(esquema_version=2, reglas_disparadas=[EMBEBIDA], politica=POLITICA)
    documento = creada.a_documento()

    assert documento["esquema_version"] == 2
    assert documento["reglas_disparadas"] == [EMBEBIDA]
    assert documento["politica"] == POLITICA


def prueba_por_omision_se_escribe_la_version_2() -> None:
    from app.modelos import ESQUEMA_ALERTA_VIGENTE

    assert ESQUEMA_ALERTA_VIGENTE == 2
    assert Alerta.model_fields["esquema_version"].default == 2


def prueba_lectura_dual_el_mismo_modelo_lee_v1_y_v2() -> None:
    """El criterio de H-26: el sistema lee las dos versiones durante la transicion."""
    v1 = Alerta.model_validate(alerta().a_documento())
    v2 = Alerta.model_validate(
        alerta(esquema_version=2, reglas_disparadas=[EMBEBIDA], politica=POLITICA).a_documento()
    )

    assert v1.regla_ids == v2.regla_ids == ["REG-001"]
    assert v2.reglas_aplicadas[0].peso == 35


@pytest.mark.parametrize(
    "mezcla",
    [
        pytest.param({"esquema_version": 2, "reglas_disparadas": ["REG-001"]}, id="v2-con-ids"),
        pytest.param(
            {"esquema_version": 2, "reglas_disparadas": [EMBEBIDA]}, id="v2-sin-politica"
        ),
        pytest.param({"esquema_version": 1, "reglas_disparadas": [EMBEBIDA]}, id="v1-embebida"),
        pytest.param(
            {"esquema_version": 1, "reglas_disparadas": ["REG-001"], "politica": POLITICA},
            id="v1-con-politica",
        ),
        pytest.param(
            {"esquema_version": 2, "reglas_disparadas": [EMBEBIDA, "REG-002"], "politica": POLITICA},
            id="lista-mezclada",
        ),
        pytest.param({"esquema_version": 3}, id="version-desconocida"),
        pytest.param(
            {
                "esquema_version": 2,
                "reglas_disparadas": [{**EMBEBIDA, "explicacion": ""}],
                "politica": POLITICA,
            },
            id="v2-sin-explicacion",
        ),
    ],
)
def prueba_una_mezcla_de_versiones_se_rechaza(mezcla: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        alerta(**mezcla)


def prueba_el_puntaje_va_de_0_a_100() -> None:
    for invalido in (-1, 101):
        with pytest.raises(ValidationError):
            alerta(puntaje=invalido)


def prueba_el_cliente_exige_formato_de_cedula() -> None:
    with pytest.raises(ValidationError):
        Cliente(
            _id="CLI-001",
            nombre="Maria Rodriguez Vargas",
            cedula="111111111",
            fecha_registro=AHORA,
        )


def prueba_el_perfil_usa_el_cliente_como_id() -> None:
    perfil = PerfilComportamiento(
        _id="CLI-001",
        promedio_crc=45_200,
        maximo_crc=180_000,
        num_transferencias=47,
        horario_habitual={"inicio": "07:00", "fin": "19:00"},
    )

    assert perfil.a_documento()["_id"] == "CLI-001"


def prueba_el_horario_habitual_es_hora_local_con_formato() -> None:
    with pytest.raises(ValidationError):
        PerfilComportamiento(
            _id="CLI-001",
            promedio_crc=1,
            maximo_crc=1,
            num_transferencias=1,
            horario_habitual={"inicio": "7am", "fin": "19:00"},
        )


def prueba_una_zona_distinta_de_la_de_costa_rica_tambien_se_normaliza() -> None:
    """El contrato es UTC en la base, no "la zona de quien escribio"."""
    tokio = timezone(timedelta(hours=9))
    guardada = transaccion(fecha=datetime(2026, 9, 25, 13, 47, tzinfo=tokio)).fecha

    assert guardada == datetime(2026, 9, 25, 4, 47, tzinfo=UTC)


def prueba_cuenta_y_transaccion_aceptan_los_campos_opcionales_del_generador() -> None:
    """T-28: `entidad` (cuenta) y `simulacion` (transaccion) son opcionales.

    Salen del documento solo si estan: con `exclude_none` el validador no ve un nulo.
    La etiqueta de simulacion es lo que el motor nunca lee.
    """
    from app.modelos import Cuenta

    sin = Cuenta(_id="CTA-001", cuenta_sinpe="8712-4455", cliente_id="CLI-001", esquema_version=1)
    con = Cuenta(
        _id="CTA-001",
        cuenta_sinpe="8712-4455",
        cliente_id="CLI-001",
        entidad="Banco Nacional",
        esquema_version=1,
    )
    assert "entidad" not in sin.a_documento()
    assert con.a_documento()["entidad"] == "Banco Nacional"

    etiquetada = transaccion(simulacion={"fraude": True, "patron": "rafaga", "episodio": 1})
    assert etiquetada.a_documento()["simulacion"]["fraude"] is True
    assert "simulacion" not in transaccion().a_documento()
