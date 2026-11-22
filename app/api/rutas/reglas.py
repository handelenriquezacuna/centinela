"""Gestion de reglas desde la API (H-16).

El supervisor ajusta el motor durante el turno sin tocar la base a mano ni desplegar:
umbrales, pesos y parametros viven en `reglas_deteccion`, y esta es la puerta para
cambiarlos.

- Editar versiona: `version` sube en uno y la anterior queda completa en
  `versiones_anteriores` del mismo documento. Una alerta guarda `regla_id` y `version`
  con que se evaluo, asi que se sigue pudiendo explicar aunque la regla cambie despues.
- Desactivar es `PATCH {"activa": false}` y no una ruta aparte: es una edicion mas, pasa
  por el mismo versionado y la misma concurrencia optimista, y reactivar es simetrico.
- Un `tipo` que el motor no sabe evaluar no entra (422 con la lista de soportados).
- `POST /reglas/simular` evalua sin escribir: probar un umbral antes de aplicarlo.

Sin autenticacion por ahora: quien puede editar reglas (rol supervisor) es H-25.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, status
from pydantic import ValidationError

from app.api.dependencias import RepoReglasDep
from app.api.errores import (
    CodigoError,
    DetalleError,
    ErrorCentinela,
    respuestas_documentadas,
)
from app.api.esquemas import (
    EdicionRegla,
    ListaReglas,
    ListaTipos,
    NuevaRegla,
    ParametroTipo,
    PeticionSimulacion,
    TipoSoportado,
)
from app.deteccion import Evaluacion, evaluar
from app.deteccion.evaluador import EVALUADORES, TIPOS_REGLA
from app.modelos import ESQUEMA_VERSION
from app.modelos.transaccional import Regla
from app.repos.carga_demo import SUFIJO_DINERO
from app.repos.reglas import CodigoDuplicado

enrutador = APIRouter(prefix="/reglas", tags=["reglas"])

ReglaIdRuta = Annotated[
    str, Path(pattern=r"^REG-\d{3}$", description="Identificador de la regla, REG-001")
]


def _tipos_soportados() -> str:
    return ", ".join(sorted(EVALUADORES))


def _exigir_tipo_y_parametros(tipo: str, parametros: dict[str, Any]) -> None:
    """422 si el motor no sabria evaluar la regla tal como quedaria guardada.

    Un tipo desconocido o un parametro mal escrito no revientan el motor (la regla sale
    como no evaluada), pero si dejan de detectar en silencio. Se cortan aca, al escribir.
    """
    if tipo not in EVALUADORES:
        raise ErrorCentinela(
            CodigoError.PARAMETROS_INVALIDOS,
            f"Tipo de regla desconocido: {tipo}. Tipos soportados: {_tipos_soportados()}",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [DetalleError(campo="tipo", mensaje=f"soportados: {_tipos_soportados()}")],
        )

    declarados = set(TIPOS_REGLA[tipo].parametros)
    detalles = [
        DetalleError(campo=f"parametros.{nombre}", mensaje=f"el tipo {tipo} lo necesita")
        for nombre in sorted(declarados - set(parametros))
    ]
    detalles += [
        DetalleError(
            campo=f"parametros.{nombre}",
            mensaje=f"el tipo {tipo} no lo lee; lee: {', '.join(sorted(declarados)) or 'ninguno'}",
        )
        for nombre in sorted(set(parametros) - declarados)
    ]
    for nombre, valor in sorted(parametros.items()):
        if valor < 0:
            detalles.append(
                DetalleError(campo=f"parametros.{nombre}", mensaje="no puede ser negativo")
            )
        if nombre.endswith(SUFIJO_DINERO) and not isinstance(valor, int):
            detalles.append(
                DetalleError(
                    campo=f"parametros.{nombre}", mensaje="es dinero: colones enteros"
                )
            )
    if detalles:
        raise ErrorCentinela(
            CodigoError.PARAMETROS_INVALIDOS,
            f"Los parametros no corresponden al tipo {tipo}",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detalles,
        )


def _no_encontrada(regla_id: str) -> ErrorCentinela:
    return ErrorCentinela(
        CodigoError.NO_ENCONTRADO,
        f"No existe la regla {regla_id}",
        status.HTTP_404_NOT_FOUND,
    )


def _version_cambiada(regla_id: str, version: Any) -> ErrorCentinela:
    return ErrorCentinela(
        CodigoError.CONFLICTO,
        f"La regla {regla_id} cambio mientras se editaba; volver a leerla y reintentar",
        status.HTTP_409_CONFLICT,
        [DetalleError(campo="version", mensaje=f"version actual: {version}")],
    )


@enrutador.get(
    "",
    response_model=ListaReglas,
    summary="Lista el catalogo de reglas, con su historia de versiones",
    responses=respuestas_documentadas(
        status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_500_INTERNAL_SERVER_ERROR
    ),
)
async def listar_reglas(
    repo: RepoReglasDep,
    activa: Annotated[
        bool | None, Query(description="Solo las activas (true) o las apagadas (false)")
    ] = None,
) -> ListaReglas:
    """Ordenadas por codigo. Sin autenticacion hasta H-25."""
    documentos = await repo.listar(activa=activa)
    return ListaReglas(
        total=len(documentos),
        elementos=[Regla.model_validate(d) for d in documentos],
    )


@enrutador.get(
    "/tipos",
    response_model=ListaTipos,
    summary="Tipos de regla que el motor sabe evaluar y que lee cada uno",
    responses=respuestas_documentadas(status.HTTP_500_INTERNAL_SERVER_ERROR),
)
async def listar_tipos() -> ListaTipos:
    """Sale de `app.deteccion.evaluador`: si el motor aprende un tipo, aparece aca."""
    return ListaTipos(
        elementos=[
            TipoSoportado(
                tipo=tipo,
                umbral=declaracion.umbral,
                parametros=[
                    ParametroTipo(nombre=nombre, descripcion=descripcion)
                    for nombre, descripcion in declaracion.parametros.items()
                ],
                evaluable=declaracion.evaluable,
            )
            for tipo, declaracion in TIPOS_REGLA.items()
        ]
    )


@enrutador.post(
    "/simular",
    response_model=Evaluacion,
    summary="Evalua una transaccion con reglas y politica propuestas, sin escribir",
    responses=respuestas_documentadas(
        status.HTTP_409_CONFLICT,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def simular(peticion: PeticionSimulacion, repo: RepoReglasDep) -> Evaluacion:
    """La misma funcion `evaluar` que usa el motor, con lo que se le proponga.

    Las reglas propuestas se superponen al catalogo de la base por `_id`; la politica
    propuesta reemplaza a la vigente. No escribe nada. Sin autenticacion hasta H-25.
    """
    reglas = [Regla.model_validate(d) for d in await repo.listar(activa=None)]
    for propuesta in peticion.reglas or []:
        indice = next(
            (i for i, r in enumerate(reglas) if r.regla_id == propuesta.regla_id), None
        )
        if indice is None:
            reglas.append(propuesta)
        else:
            reglas[indice] = propuesta

    politica = peticion.politica
    if politica is None:
        politica = await repo.politica_vigente()
        if politica is None:
            raise ErrorCentinela(
                CodigoError.CONFLICTO,
                "No hay politica_deteccion vigente; mandar una en `politica`",
                status.HTTP_409_CONFLICT,
            )

    return evaluar(peticion.transaccion, peticion.contexto, reglas, politica)


@enrutador.get(
    "/{regla_id}",
    response_model=Regla,
    summary="Una regla, con sus versiones anteriores",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def obtener_regla(regla_id: ReglaIdRuta, repo: RepoReglasDep) -> Regla:
    """Sin autenticacion hasta H-25."""
    documento = await repo.obtener(regla_id)
    if documento is None:
        raise _no_encontrada(regla_id)
    return Regla.model_validate(documento)


@enrutador.post(
    "",
    response_model=Regla,
    status_code=status.HTTP_201_CREATED,
    summary="Crea una regla en version 1",
    responses=respuestas_documentadas(
        status.HTTP_409_CONFLICT,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def crear_regla(nueva: NuevaRegla, repo: RepoReglasDep) -> Regla:
    """El servidor asigna el siguiente REG-NNN. Codigo repetido: 409. Sin
    autenticacion hasta H-25."""
    _exigir_tipo_y_parametros(nueva.tipo, nueva.parametros)
    documento = {
        **nueva.model_dump(),
        "version": 1,
        "esquema_version": ESQUEMA_VERSION,
    }
    try:
        creada = await repo.crear(documento)
    except CodigoDuplicado as fallo:
        raise ErrorCentinela(
            CodigoError.CONFLICTO,
            f"Ya existe una regla con el codigo {nueva.codigo}",
            status.HTTP_409_CONFLICT,
            [DetalleError(campo="codigo", mensaje="tiene que ser unico")],
        ) from fallo
    return Regla.model_validate(creada)


@enrutador.patch(
    "/{regla_id}",
    response_model=Regla,
    summary="Edita una regla (umbral, parametros, peso, activa, descripcion) y la versiona",
    responses=respuestas_documentadas(
        status.HTTP_404_NOT_FOUND,
        status.HTTP_409_CONFLICT,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
    ),
)
async def editar_regla(
    regla_id: ReglaIdRuta, edicion: EdicionRegla, repo: RepoReglasDep
) -> Regla:
    """Una sola escritura atomica, filtrada por la version leida.

    - `version` sube en uno; la anterior queda en `versiones_anteriores`.
    - Si otro la edito en el medio, o `version_esperada` no coincide: 409, sin escribir.
    - Campos que llegan iguales a lo guardado no cuentan; si no cambia nada, se
      devuelve la regla tal cual y no se crea version.
    - Desactivar: `{"activa": false}`.

    Sin autenticacion hasta H-25.
    """
    cambios = edicion.cambios()
    if not cambios:
        raise ErrorCentinela(
            CodigoError.PARAMETROS_INVALIDOS,
            "La edicion no trae ningun campo para cambiar",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    actual = await repo.obtener(regla_id)
    if actual is None:
        raise _no_encontrada(regla_id)
    if edicion.version_esperada is not None and edicion.version_esperada != actual.get(
        "version"
    ):
        raise _version_cambiada(regla_id, actual.get("version"))

    efectivos = {campo: valor for campo, valor in cambios.items() if actual.get(campo) != valor}
    if not efectivos:
        return Regla.model_validate(actual)

    if "parametros" in efectivos:
        _exigir_tipo_y_parametros(actual["tipo"], efectivos["parametros"])
    try:
        Regla.model_validate({**actual, **efectivos})
    except ValidationError as fallo:
        raise ErrorCentinela(
            CodigoError.PARAMETROS_INVALIDOS,
            "La regla quedaria invalida con esa edicion",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [
                DetalleError(campo=".".join(map(str, e["loc"])) or None, mensaje=e["msg"])
                for e in fallo.errors()
            ],
        ) from fallo

    editada = await repo.editar(actual, efectivos, datetime.now(UTC))
    if editada is None:
        vigente = await repo.obtener(regla_id)
        if vigente is None:
            raise _no_encontrada(regla_id)
        raise _version_cambiada(regla_id, vigente.get("version"))
    return Regla.model_validate(editada)
