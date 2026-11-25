"""D-74: el manifiesto, la comparacion de conteos y el servicio de respaldos, sin Mongo.

Las herramientas (`mongodump`, `mongorestore`) se sustituyen por scripts de shell en un
directorio temporal que va primero en el PATH: asi se prueba lo que el servicio hace con los
subprocesos (argumentos, URI fuera de la linea de comandos, archivo parcial borrado, tiempo
maximo, una operacion a la vez) sin necesitar una base. Lo que dicen de verdad las
herramientas contra un Mongo esta en `pruebas/integracion/prueba_respaldos_mongo.py`.
"""

from __future__ import annotations

import asyncio
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.nucleo.config import Ajustes
from app.respaldos import manifiesto as mf
from app.respaldos.servicio import (
    HerramientaFallida,
    OperacionEnCurso,
    RespaldoNoEncontrado,
    ServicioRespaldos,
    base_temporal,
)

URI_SECRETA = "mongodb://centinela_respaldo:s3creta@localhost:27018/?replicaSet=rsfraude"


# ---------------------------------------------------------------------------
# El manifiesto y la comparacion de conteos
# ---------------------------------------------------------------------------


def prueba_el_id_es_legible_ordenable_y_cumple_su_patron() -> None:
    identificador = mf.nuevo_id(datetime(2026, 11, 24, 15, 30, 12, tzinfo=UTC))

    assert identificador == "RSP-20261124T153012Z"
    assert mf.es_id_valido(identificador)
    assert mf.nuevo_id(datetime(2026, 11, 25, tzinfo=UTC)) > identificador


@pytest.mark.parametrize(
    "valor", ["", "RSP-1", "../etc/passwd", "RSP-20261124T153012Z/../x", "rsp-20261124T153012Z"]
)
def prueba_un_id_con_forma_de_ruta_no_es_valido(valor: str) -> None:
    assert not mf.es_id_valido(valor)


def prueba_conteos_iguales_no_dan_diferencias() -> None:
    conteos = {"alertas": 10, "transacciones": 90}

    assert mf.comparar_conteos(conteos, conteos, dict(conteos)) == []


def prueba_un_conteo_distinto_es_una_diferencia_con_su_coleccion() -> None:
    antes = {"alertas": 10, "transacciones": 90}

    diferencias = mf.comparar_conteos(antes, antes, {"alertas": 10, "transacciones": 80})

    assert [(d.coleccion, d.esperados_desde, d.esperados_hasta, d.restaurados) for d in diferencias] == [
        ("transacciones", 90, 90, 80)
    ]


def prueba_si_la_base_cambio_durante_el_dump_se_acepta_lo_que_cae_entre_los_dos_conteos() -> None:
    antes = {"transacciones": 90}
    despues = {"transacciones": 95}

    assert mf.comparar_conteos(antes, despues, {"transacciones": 92}) == []
    assert mf.comparar_conteos(antes, despues, {"transacciones": 90}) == []
    assert mf.comparar_conteos(antes, despues, {"transacciones": 95}) == []
    assert len(mf.comparar_conteos(antes, despues, {"transacciones": 96})) == 1
    assert len(mf.comparar_conteos(antes, despues, {"transacciones": 89})) == 1


def prueba_una_coleccion_que_falta_o_sobra_se_detecta() -> None:
    antes = {"alertas": 3}

    faltante = mf.comparar_conteos(antes, antes, {})
    sobrante = mf.comparar_conteos(antes, antes, {"alertas": 3, "extra": 1})

    assert [d.coleccion for d in faltante] == ["alertas"] and faltante[0].restaurados == 0
    assert [d.coleccion for d in sobrante] == ["extra"]


def prueba_una_coleccion_vacia_que_no_se_restauro_no_es_diferencia() -> None:
    assert mf.comparar_conteos({"vacia": 0}, {"vacia": 0}, {}) == []


def _manifiesto(**campos: object) -> mf.Manifiesto:
    base: dict[str, object] = {
        "respaldo_id": "RSP-20261124T153012Z",
        "creado_utc": datetime(2026, 11, 24, 15, 30, 12, tzinfo=UTC),
        "creado_por": "AGT-900",
        "base": "antifraude",
        "archivo": "RSP-20261124T153012Z.gz",
        "tamano_bytes": 1234,
        "sha256": "a" * 64,
        "herramienta": "mongodump version: 100.20.1",
        "documentos_por_coleccion": {"alertas": 10, "transacciones": 90},
        "documentos_al_terminar": {"alertas": 10, "transacciones": 90},
    }
    base.update(campos)
    return mf.Manifiesto(**base)


def prueba_el_manifiesto_lleva_fecha_utc_tamano_conteos_autor_y_esquema(tmp_path: Path) -> None:
    ruta = mf.escribir(tmp_path, _manifiesto())
    escrito = json.loads(ruta.read_text(encoding="utf-8"))

    assert ruta.name == "RSP-20261124T153012Z.json"
    assert escrito["creado_utc"].endswith("Z")
    assert escrito["tamano_bytes"] == 1234
    assert escrito["documentos_por_coleccion"] == {"alertas": 10, "transacciones": 90}
    assert escrito["creado_por"] == "AGT-900"
    assert escrito["esquema_version"] == 1
    assert escrito["verificacion"] is None
    assert not list(tmp_path.glob("*.tmp")), "el renombrado atomico no deja el temporal"


def prueba_el_manifiesto_se_lee_igual_que_se_escribio(tmp_path: Path) -> None:
    original = _manifiesto()

    leido = mf.leer(mf.escribir(tmp_path, original))

    assert leido == original
    assert leido.total_documentos == 100


def prueba_el_manifiesto_rechaza_un_sha256_o_un_id_mal_formados() -> None:
    with pytest.raises(ValueError):
        _manifiesto(sha256="xyz")
    with pytest.raises(ValueError):
        _manifiesto(respaldo_id="../../x")


def prueba_la_base_temporal_es_la_base_mas_un_sufijo_y_nunca_la_propia() -> None:
    assert base_temporal("antifraude") == "antifraude_verificacion"
    assert base_temporal("antifraude") != "antifraude"


# ---------------------------------------------------------------------------
# El servicio, con herramientas de mentira
# ---------------------------------------------------------------------------


class RepoFalso:
    def __init__(self, conteos: dict[str, dict[str, int]] | None = None) -> None:
        self.conteos = conteos or {"antifraude": {"alertas": 2}}
        self.borradas: list[str] = []
        # Lo que dejaria `mongorestore` en la base temporal. Se ve despues del primer borrado
        # (la limpieza previa) y deja de verse con el segundo (el borrado final).
        self.restaurado: dict[str, int] | None = None

    async def contar_documentos(self, base: str) -> dict[str, int]:
        if base == "antifraude_verificacion" and self.restaurado is not None:
            return dict(self.restaurado) if len(self.borradas) == 1 else {}
        return dict(self.conteos.get(base, {}))

    async def borrar_base(self, base: str) -> None:
        self.borradas.append(base)
        self.conteos.pop(base, None)


def _herramienta(directorio: Path, nombre: str, cuerpo: str) -> None:
    ruta = directorio / nombre
    ruta.write_text("#!/bin/sh\n" + cuerpo, encoding="utf-8")
    ruta.chmod(ruta.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def herramientas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`mongodump` y `mongorestore` falsos, primeros en el PATH. Anotan sus argumentos."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    registro = tmp_path / "llamadas.log"
    _herramienta(
        bin_,
        "mongodump",
        'if [ "$1" = "--version" ]; then echo "mongodump version: 100.20.1"; exit 0; fi\n'
        f'echo "mongodump $@" >> "{registro}"\n'
        # copia lo que haya en --config para saber que la URI viajo ahi, no en la linea
        f'for a in "$@"; do case "$a" in --config=*) cat "${{a#--config=}}" >> "{registro}";; '
        'esac; done\n'
        'for a in "$@"; do case "$a" in --archive=*) printf "datos" > "${a#--archive=}";; esac; done\n',
    )
    _herramienta(
        bin_,
        "mongorestore",
        f'echo "mongorestore $@" >> "{registro}"\n',
    )
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    return registro


def _servicio(tmp_path: Path, repo: RepoFalso | None = None, **respaldos: object) -> ServicioRespaldos:
    ajustes = Ajustes(
        mongo={"uri": URI_SECRETA, "base": "antifraude"},
        respaldos={"directorio": str(tmp_path / "respaldos"), **respaldos},
    )
    return ServicioRespaldos(repo or RepoFalso(), ajustes)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def prueba_crear_deja_archivo_y_manifiesto_con_el_autor_y_los_conteos(
    tmp_path: Path, herramientas: Path
) -> None:
    servicio = _servicio(tmp_path)

    manifiesto = await servicio.crear("AGT-900")

    directorio = tmp_path / "respaldos"
    assert (directorio / manifiesto.archivo).read_bytes() == b"datos"
    assert (directorio / f"{manifiesto.respaldo_id}.json").is_file()
    assert manifiesto.creado_por == "AGT-900"
    assert manifiesto.base == "antifraude"
    assert manifiesto.tamano_bytes == 5
    assert manifiesto.documentos_por_coleccion == {"alertas": 2}
    assert manifiesto.herramienta == "mongodump version: 100.20.1"
    assert [m.respaldo_id for m in await servicio.listar()] == [manifiesto.respaldo_id]


@pytest.mark.asyncio
async def prueba_la_uri_con_credenciales_va_en_un_archivo_y_no_en_la_linea_de_comandos(
    tmp_path: Path, herramientas: Path
) -> None:
    servicio = _servicio(tmp_path)

    await servicio.crear("AGT-900")

    llamadas = herramientas.read_text(encoding="utf-8")
    linea = next(x for x in llamadas.splitlines() if x.startswith("mongodump --config="))
    assert "s3creta" not in linea, "la clave se veria en `ps`"
    assert "--gzip" in linea and "--db=antifraude" in linea and "--archive=" in linea
    assert f'uri: "{URI_SECRETA}"' in llamadas, "la URI viajo en el archivo de --config"
    config = linea.split("--config=")[1].split()[0]
    assert not Path(config).exists(), "el archivo con la clave se borra al terminar"


@pytest.mark.asyncio
async def prueba_la_uri_de_respaldos_gana_sobre_la_de_la_app(
    tmp_path: Path, herramientas: Path
) -> None:
    otra = "mongodb://centinela_respaldo:otra@localhost:27018/?replicaSet=rsfraude"
    servicio = _servicio(tmp_path, uri=otra)

    await servicio.crear("AGT-900")

    llamadas = herramientas.read_text(encoding="utf-8")
    assert f'uri: "{otra}"' in llamadas
    assert URI_SECRETA not in llamadas


@pytest.mark.asyncio
async def prueba_si_mongodump_falla_no_queda_archivo_ni_manifiesto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, herramientas: Path
) -> None:
    _herramienta(
        tmp_path / "bin",
        "mongodump",
        'for a in "$@"; do case "$a" in --archive=*) printf "x" > "${a#--archive=}";; esac; done\n'
        "echo 'no se pudo conectar' >&2\nexit 7\n",
    )
    servicio = _servicio(tmp_path)

    with pytest.raises(HerramientaFallida, match="codigo 7.*no se pudo conectar"):
        await servicio.crear("AGT-900")

    assert list((tmp_path / "respaldos").iterdir()) == []


@pytest.mark.asyncio
async def prueba_sin_la_herramienta_el_error_dice_cual_falta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # sin mongodump
    servicio = _servicio(tmp_path)

    with pytest.raises(HerramientaFallida, match="no se encontro mongodump"):
        await servicio.crear("AGT-900")


@pytest.mark.asyncio
async def prueba_una_herramienta_que_tarda_mas_del_tope_se_mata(
    tmp_path: Path, herramientas: Path
) -> None:
    _herramienta(tmp_path / "bin", "mongodump", "sleep 30\n")
    servicio = _servicio(tmp_path, tiempo_maximo_segundos=10)
    servicio._ajustes.respaldos.tiempo_maximo_segundos = 1  # el tope minimo valido es 10

    with pytest.raises(HerramientaFallida, match="supero 1 s"):
        await servicio.crear("AGT-900")


@pytest.mark.asyncio
async def prueba_una_operacion_a_la_vez_la_segunda_es_conflicto(
    tmp_path: Path, herramientas: Path
) -> None:
    _herramienta(
        tmp_path / "bin",
        "mongodump",
        'if [ "$1" = "--version" ]; then echo v; exit 0; fi\n'
        'for a in "$@"; do case "$a" in --archive=*) printf "d" > "${a#--archive=}";; esac; done\n'
        "sleep 1\n",
    )
    servicio = _servicio(tmp_path)

    primera = asyncio.create_task(servicio.crear("AGT-900"))
    await asyncio.sleep(0.2)
    assert servicio.en_curso

    with pytest.raises(OperacionEnCurso):
        await servicio.crear("AGT-901")
    with pytest.raises(OperacionEnCurso):
        await servicio.verificar("RSP-20261124T153012Z", "AGT-901")
    await primera
    assert not servicio.en_curso


@pytest.mark.asyncio
async def prueba_las_herramientas_no_bloquean_el_bucle_de_eventos(
    tmp_path: Path, herramientas: Path
) -> None:
    _herramienta(
        tmp_path / "bin",
        "mongodump",
        'if [ "$1" = "--version" ]; then echo v; exit 0; fi\n'
        'for a in "$@"; do case "$a" in --archive=*) printf "d" > "${a#--archive=}";; esac; done\n'
        "sleep 1\n",
    )
    servicio = _servicio(tmp_path)
    latidos = 0

    async def latir() -> None:
        nonlocal latidos
        while True:
            await asyncio.sleep(0.05)
            latidos += 1

    tarea = asyncio.create_task(latir())
    await servicio.crear("AGT-900")
    tarea.cancel()

    assert latidos >= 10, "el bucle siguio corriendo mientras duraba el respaldo"


@pytest.mark.asyncio
async def prueba_verificar_restaura_en_la_temporal_compara_y_la_borra(
    tmp_path: Path, herramientas: Path
) -> None:
    repo = RepoFalso()
    servicio = _servicio(tmp_path, repo)
    creado = await servicio.crear("AGT-900")
    repo.restaurado = {"alertas": 2}

    verificado = await servicio.verificar(creado.respaldo_id, "AGT-901")

    resultado = verificado.verificacion
    assert resultado is not None
    assert resultado.resultado == "correcta"
    assert resultado.verificado_por == "AGT-901"
    assert resultado.base_temporal == "antifraude_verificacion"
    assert resultado.base_temporal_borrada
    assert repo.borradas[-1] == "antifraude_verificacion"
    assert "antifraude" not in repo.borradas, "jamas se borra la base viva"
    restore = next(
        x for x in herramientas.read_text(encoding="utf-8").splitlines()
        if x.startswith("mongorestore")
    )
    assert "--nsFrom=antifraude.*" in restore and "--nsTo=antifraude_verificacion.*" in restore
    assert "s3creta" not in restore
    # y el resultado quedo en el manifiesto del disco
    assert mf.leer(tmp_path / "respaldos" / f"{creado.respaldo_id}.json").verificacion == resultado


@pytest.mark.asyncio
async def prueba_verificar_con_conteos_distintos_es_fallida_y_borra_igual(
    tmp_path: Path, herramientas: Path
) -> None:
    repo = RepoFalso()
    servicio = _servicio(tmp_path, repo)
    creado = await servicio.crear("AGT-900")
    repo.restaurado = {"alertas": 1}

    verificado = await servicio.verificar(creado.respaldo_id, "AGT-900")

    resultado = verificado.verificacion
    assert resultado is not None and resultado.resultado == "fallida"
    assert [(d.coleccion, d.restaurados) for d in resultado.diferencias] == [("alertas", 1)]
    assert resultado.base_temporal_borrada
    assert await repo.contar_documentos("antifraude_verificacion") == {}


@pytest.mark.asyncio
async def prueba_verificar_un_archivo_alterado_es_fallida_y_no_restaura(
    tmp_path: Path, herramientas: Path
) -> None:
    servicio = _servicio(tmp_path)
    creado = await servicio.crear("AGT-900")
    (tmp_path / "respaldos" / creado.archivo).write_bytes(b"alterado!")

    verificado = await servicio.verificar(creado.respaldo_id, "AGT-900")

    resultado = verificado.verificacion
    assert resultado is not None and resultado.resultado == "fallida"
    assert "no coincide" in (resultado.detalle or "")
    assert "mongorestore" not in herramientas.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def prueba_si_mongorestore_falla_la_verificacion_es_fallida_y_la_temporal_se_borra(
    tmp_path: Path, herramientas: Path
) -> None:
    servicio = _servicio(tmp_path)
    creado = await servicio.crear("AGT-900")
    _herramienta(tmp_path / "bin", "mongorestore", "echo 'index invalido' >&2\nexit 3\n")

    verificado = await servicio.verificar(creado.respaldo_id, "AGT-900")

    resultado = verificado.verificacion
    assert resultado is not None and resultado.resultado == "fallida"
    assert "index invalido" in (resultado.detalle or "")
    assert resultado.base_temporal_borrada


@pytest.mark.asyncio
async def prueba_verificar_un_respaldo_inexistente_o_con_forma_de_ruta_es_no_encontrado(
    tmp_path: Path, herramientas: Path
) -> None:
    servicio = _servicio(tmp_path)

    with pytest.raises(RespaldoNoEncontrado):
        await servicio.verificar("RSP-20200101T000000Z", "AGT-900")
    with pytest.raises(RespaldoNoEncontrado):
        await servicio.verificar("../../etc/passwd", "AGT-900")


@pytest.mark.asyncio
async def prueba_listar_omite_un_manifiesto_ilegible_y_ordena_el_mas_reciente_primero(
    tmp_path: Path,
) -> None:
    directorio = tmp_path / "respaldos"
    directorio.mkdir()
    mf.escribir(directorio, _manifiesto(respaldo_id="RSP-20261101T000000Z"))
    mf.escribir(directorio, _manifiesto(respaldo_id="RSP-20261120T000000Z"))
    (directorio / "RSP-20261110T000000Z.json").write_text("{no es json", encoding="utf-8")

    listados = await _servicio(tmp_path).listar()

    assert [m.respaldo_id for m in listados] == ["RSP-20261120T000000Z", "RSP-20261101T000000Z"]


@pytest.mark.asyncio
async def prueba_sin_directorio_la_lista_esta_vacia(tmp_path: Path) -> None:
    assert await _servicio(tmp_path).listar() == []
