"""Crear, verificar y listar respaldos (D-74).

Que hace cada operacion:

- Crear: cuenta los documentos por coleccion, corre `mongodump --archive=<id>.gz --gzip
  --db <base>`, vuelve a contar, calcula tamano y sha256 y escribe el manifiesto JSON.
- Verificar: restaura el archivo en una base temporal (`<base>_verificacion`) con
  `mongorestore --nsFrom/--nsTo`, compara los conteos con el manifiesto, BORRA la base
  temporal (siempre, tambien si algo falla) y guarda el resultado en el manifiesto.

Reglas que este modulo hace cumplir:

- Las herramientas corren como subprocesos asincronos: el bucle de eventos (D1, el canal
  SSE) no se bloquea mientras dura un respaldo.
- Una operacion a la vez por proceso (`OperacionEnCurso`, 409 en la API). Un solo worker
  es una decision de arquitectura, asi que el cerrojo en memoria alcanza.
- La URI con credenciales no viaja en la linea de comandos (se veria en `ps`): va en un
  archivo temporal de permisos 600 que se pasa con `--config` y se borra al terminar.
- La base temporal tiene nombre fijo (`<base>_verificacion`) y no uno por respaldo porque
  el usuario `centinela_respaldo` solo tiene permisos sobre esa base (04-usuarios.js); y
  una a la vez es ademas la regla.
- Restaurar sobre la base viva NO esta aqui: es `python tareas.py restaurar`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import signal
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.nucleo.config import Ajustes
from app.repos.respaldos import RepoRespaldos
from app.respaldos import manifiesto as mf
from app.respaldos.manifiesto import Manifiesto, Verificacion

registro = logging.getLogger("centinela.respaldos")
auditoria = logging.getLogger("centinela.auditoria")

SUFIJO_BASE_TEMPORAL = "_verificacion"
COLA_DE_SALIDA = 600  # caracteres de la salida de la herramienta que se conservan en un error


class ErrorRespaldo(Exception):
    """Base de los fallos esperados de un respaldo."""


class RespaldoNoEncontrado(ErrorRespaldo):
    pass


class OperacionEnCurso(ErrorRespaldo):
    pass


class HerramientaFallida(ErrorRespaldo):
    pass


def base_temporal(base: str) -> str:
    return f"{base}{SUFIJO_BASE_TEMPORAL}"


def _sha256_y_tamano(ruta: Path) -> tuple[str, int]:
    resumen = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            resumen.update(bloque)
    return resumen.hexdigest(), ruta.stat().st_size


def _quitar(ruta: Path) -> None:
    with contextlib.suppress(OSError):
        ruta.unlink()


class ServicioRespaldos:
    def __init__(self, repo: RepoRespaldos, ajustes: Ajustes) -> None:
        self._repo = repo
        self._ajustes = ajustes
        self._cerrojo = asyncio.Lock()
        self._version_herramienta: str | None = None

    @property
    def directorio(self) -> Path:
        return self._ajustes.respaldos.ruta_directorio

    @property
    def base(self) -> str:
        return self._ajustes.mongo.base

    @property
    def en_curso(self) -> bool:
        return self._cerrojo.locked()

    # ----------------------------------------------------------------- listar

    async def listar(self) -> list[Manifiesto]:
        """Los respaldos conocidos, el mas reciente primero. Un manifiesto ilegible se omite."""
        return await asyncio.to_thread(self._listar)

    def _listar(self) -> list[Manifiesto]:
        if not self.directorio.is_dir():
            return []
        encontrados: list[Manifiesto] = []
        for ruta in sorted(self.directorio.glob("RSP-*.json"), reverse=True):
            try:
                encontrados.append(mf.leer(ruta))
            except (OSError, ValueError):
                registro.warning("manifiesto ilegible, se omite: %s", ruta.name)
        return encontrados

    async def obtener(self, respaldo_id: str) -> Manifiesto:
        if not mf.es_id_valido(respaldo_id):
            raise RespaldoNoEncontrado(f"no existe el respaldo {respaldo_id}")
        ruta = mf.ruta_manifiesto(self.directorio, respaldo_id)
        try:
            return await asyncio.to_thread(mf.leer, ruta)
        except (OSError, ValueError) as fallo:
            raise RespaldoNoEncontrado(f"no existe el respaldo {respaldo_id}") from fallo

    # ------------------------------------------------------------------ crear

    async def crear(self, agente_id: str) -> Manifiesto:
        if self._cerrojo.locked():
            raise OperacionEnCurso("ya hay un respaldo o una verificacion en curso")
        async with self._cerrojo:
            return await self._crear(agente_id)

    async def _crear(self, agente_id: str) -> Manifiesto:
        inicio = datetime.now(UTC)
        self.directorio.mkdir(parents=True, exist_ok=True)
        respaldo_id = mf.nuevo_id(inicio)
        while mf.ruta_manifiesto(self.directorio, respaldo_id).exists():
            inicio += timedelta(seconds=1)
            respaldo_id = mf.nuevo_id(inicio)
        archivo = self.directorio / f"{respaldo_id}.gz"
        auditoria.info("respaldo iniciado id=%s base=%s por=%s", respaldo_id, self.base, agente_id)
        try:
            antes = await self._repo.contar_documentos(self.base)
            await self._herramienta(
                "mongodump", [f"--archive={archivo}", "--gzip", f"--db={self.base}"]
            )
            despues = await self._repo.contar_documentos(self.base)
            sha256, tamano = await asyncio.to_thread(_sha256_y_tamano, archivo)
            version = await self._version("mongodump")
        except BaseException as fallo:
            _quitar(archivo)
            auditoria.warning(
                "respaldo fallido id=%s base=%s por=%s motivo=%s", respaldo_id, self.base,
                agente_id, fallo,
            )
            raise
        os.chmod(archivo, 0o600)
        manifiesto = Manifiesto(
            respaldo_id=respaldo_id,
            creado_utc=inicio,
            creado_por=agente_id,
            base=self.base,
            archivo=archivo.name,
            tamano_bytes=tamano,
            sha256=sha256,
            herramienta=version,
            documentos_por_coleccion=antes,
            documentos_al_terminar=despues,
        )
        await asyncio.to_thread(mf.escribir, self.directorio, manifiesto)
        auditoria.info(
            "respaldo creado id=%s base=%s por=%s tamano_bytes=%d documentos=%d",
            respaldo_id, self.base, agente_id, tamano, manifiesto.total_documentos,
        )
        return manifiesto

    # --------------------------------------------------------------- verificar

    async def verificar(self, respaldo_id: str, agente_id: str) -> Manifiesto:
        if self._cerrojo.locked():
            raise OperacionEnCurso("ya hay un respaldo o una verificacion en curso")
        async with self._cerrojo:
            manifiesto = await self.obtener(respaldo_id)
            return await self._verificar(manifiesto, agente_id)

    async def _verificar(self, manifiesto: Manifiesto, agente_id: str) -> Manifiesto:
        temporal = base_temporal(manifiesto.base)
        archivo = self.directorio / f"{manifiesto.respaldo_id}.gz"
        auditoria.info(
            "verificacion iniciada id=%s por=%s base_temporal=%s",
            manifiesto.respaldo_id, agente_id, temporal,
        )
        inicio = time.monotonic()
        restaurados: dict[str, int] = {}
        detalle: str | None = None
        diferencias: list[mf.Diferencia] = []
        try:
            if not archivo.is_file():
                detalle = f"falta el archivo {archivo.name}"
            else:
                sha256, tamano = await asyncio.to_thread(_sha256_y_tamano, archivo)
                if sha256 != manifiesto.sha256 or tamano != manifiesto.tamano_bytes:
                    detalle = "el archivo no coincide con el manifiesto (sha256 o tamano)"
            if detalle is None:
                await self._repo.borrar_base(temporal)  # restos de una corrida anterior
                await self._herramienta(
                    "mongorestore",
                    [
                        f"--archive={archivo}",
                        "--gzip",
                        f"--nsInclude={manifiesto.base}.*",
                        f"--nsFrom={manifiesto.base}.*",
                        f"--nsTo={temporal}.*",
                        "--stopOnError",
                    ],
                )
                restaurados = await self._repo.contar_documentos(temporal)
                diferencias = mf.comparar_conteos(
                    manifiesto.documentos_por_coleccion,
                    manifiesto.documentos_al_terminar,
                    restaurados,
                )
        except ErrorRespaldo as fallo:
            detalle = str(fallo)
        finally:
            borrada = await self._borrar_temporal(temporal)
        resultado: mf.ResultadoVerificacion = (
            "correcta" if detalle is None and not diferencias and borrada else "fallida"
        )
        if detalle is None and not borrada:
            detalle = f"no se pudo borrar la base temporal {temporal}"
        manifiesto.verificacion = Verificacion(
            verificado_utc=datetime.now(UTC),
            verificado_por=agente_id,
            resultado=resultado,
            base_temporal=temporal,
            base_temporal_borrada=borrada,
            duracion_segundos=round(time.monotonic() - inicio, 3),
            documentos_restaurados=restaurados,
            diferencias=diferencias,
            detalle=detalle,
        )
        await asyncio.to_thread(mf.escribir, self.directorio, manifiesto)
        escribir = auditoria.info if resultado == "correcta" else auditoria.warning
        escribir(
            "verificacion %s id=%s por=%s diferencias=%d base_temporal_borrada=%s detalle=%s",
            resultado, manifiesto.respaldo_id, agente_id, len(diferencias), borrada, detalle,
        )
        return manifiesto

    async def _borrar_temporal(self, temporal: str) -> bool:
        """Borra la base temporal y comprueba que quedo vacia. Nunca lanza."""
        if temporal == self.base:  # defensa: jamas se borra la base viva
            return False
        try:
            await self._repo.borrar_base(temporal)
            return not await self._repo.contar_documentos(temporal)
        except Exception as fallo:  # noqa: BLE001 - se reporta en el resultado
            registro.error("no se pudo borrar %s: %s", temporal, fallo)
            return False

    # ------------------------------------------------------------ herramientas

    async def _version(self, programa: str) -> str:
        if self._version_herramienta is None:
            salida = await self._ejecutar([programa, "--version"])
            self._version_herramienta = salida.splitlines()[0].strip() if salida else programa
        return self._version_herramienta

    async def _herramienta(self, programa: str, argumentos: list[str]) -> None:
        """Corre `mongodump` o `mongorestore` con la URI en un archivo temporal 600."""
        uri = self._ajustes.respaldos.uri or self._ajustes.mongo.uri
        descriptor, nombre = tempfile.mkstemp(prefix="centinela-respaldo-", suffix=".yml")
        configuracion = Path(nombre)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
                archivo.write("uri: " + _entrecomillar(uri) + "\n")
            await self._ejecutar([programa, f"--config={configuracion}", *argumentos])
        finally:
            _quitar(configuracion)

    async def _ejecutar(self, orden: list[str]) -> str:
        try:
            proceso = await asyncio.create_subprocess_exec(
                *orden,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                # Grupo propio: al vencer el tiempo se mata el grupo entero, no solo al padre.
                start_new_session=os.name != "nt",
            )
        except FileNotFoundError as fallo:
            raise HerramientaFallida(
                f"no se encontro {orden[0]} (mongodb-database-tools) en el PATH"
            ) from fallo
        try:
            salida, _ = await asyncio.wait_for(
                proceso.communicate(), timeout=self._ajustes.respaldos.tiempo_maximo_segundos
            )
        except TimeoutError as fallo:
            _matar(proceso)
            await proceso.wait()
            raise HerramientaFallida(
                f"{orden[0]} supero {self._ajustes.respaldos.tiempo_maximo_segundos} s"
            ) from fallo
        texto = salida.decode("utf-8", "replace").strip()
        if proceso.returncode != 0:
            raise HerramientaFallida(
                f"{orden[0]} termino con codigo {proceso.returncode}: {texto[-COLA_DE_SALIDA:]}"
            )
        return texto


def _matar(proceso: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if os.name != "nt":
            os.killpg(proceso.pid, signal.SIGKILL)
        else:
            proceso.kill()


def _entrecomillar(valor: str) -> str:
    """Una cadena YAML entre comillas dobles (la URI puede traer `:`, `@` y `%`)."""
    return '"' + valor.replace("\\", "\\\\").replace('"', '\\"') + '"'
