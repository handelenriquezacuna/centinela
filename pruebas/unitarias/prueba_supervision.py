"""La supervision de tareas de fondo, sin Mongo y sin HTTP.

Es donde se verifica el criterio de H-00B que mas facil se da por hecho: que matar una
tarea a mano deje el sistema en `degradado`. Todo lo que hace falta para probarlo es
`asyncio`, porque el supervisor no sabe de bases de datos ni de pantallas.

Las tolerancias son de centesimas de segundo a proposito: una prueba que espera 30
segundos para comprobar un retraso no se corre nunca.
"""

from __future__ import annotations

import asyncio

import pytest

from app.supervision import (
    DISPARADORES_PREVISTOS,
    NOMBRE_LATIDO,
    EstadoSupervision,
    EstadoTarea,
    Supervisor,
    latido,
)

TOLERANCIA_CORTA = 0.05


async def _nunca_termina() -> None:
    """Tarea viva que no reporta nada. Sirve para probar el retraso."""
    await asyncio.sleep(3600)


@pytest.mark.asyncio
async def prueba_una_tarea_recien_lanzada_esta_corriendo() -> None:
    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar("tarea_de_prueba", _nunca_termina())

    informe = supervisor.informe()

    assert informe.estado is EstadoSupervision.OK
    fila = informe.tarea("tarea_de_prueba")
    assert fila.estado is EstadoTarea.CORRIENDO
    assert fila.eventos_procesados == 0
    # Todavia no hubo un evento: el retraso no es cero, es desconocido. Publicar cero
    # seria afirmar que acaba de procesar algo.
    assert fila.retraso_segundos is None
    assert fila.ultimo_evento is None

    await supervisor.detener()


@pytest.mark.asyncio
async def prueba_latir_cuenta_eventos_y_pone_el_retraso_en_cero() -> None:
    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar("tarea_de_prueba", _nunca_termina())

    supervisor.latir("tarea_de_prueba")
    supervisor.latir("tarea_de_prueba")

    fila = supervisor.informe().tarea("tarea_de_prueba")
    assert fila.eventos_procesados == 2
    assert fila.ultimo_evento is not None
    assert fila.ultimo_evento.tzinfo is not None, "la fecha del informe lleva zona"
    assert fila.retraso_segundos is not None and fila.retraso_segundos < 1

    await supervisor.detener()


@pytest.mark.asyncio
async def prueba_matar_una_tarea_a_mano_deja_el_sistema_degradado() -> None:
    """El criterio de la historia, tal cual esta escrito.

    Cancelar es `detenida` y no `fallida`: nadie se equivoco, alguien la apago. La
    distincion importa cuando hay que decidir si esto se investiga o se reinicia.
    """
    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar("tarea_de_prueba", _nunca_termina())
    assert supervisor.informe().estado is EstadoSupervision.OK

    await supervisor.detener_tarea("tarea_de_prueba")

    informe = supervisor.informe()
    assert informe.estado is EstadoSupervision.DEGRADADO
    assert informe.tarea("tarea_de_prueba").estado is EstadoTarea.DETENIDA


@pytest.mark.asyncio
async def prueba_una_tarea_muerta_sigue_en_el_informe() -> None:
    """Desaparecer del informe seria peor que estar en rojo: nadie extrana lo que no ve."""
    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar("tarea_de_prueba", _nunca_termina())

    await supervisor.detener_tarea("tarea_de_prueba")

    nombres = [fila.nombre for fila in supervisor.informe().tareas]
    assert "tarea_de_prueba" in nombres


@pytest.mark.asyncio
async def prueba_una_tarea_que_revienta_queda_fallida_con_su_error() -> None:
    """Sin el `done_callback` del supervisor, este error se perderia en silencio."""

    async def revienta() -> None:
        raise RuntimeError("se cayo el disparador")

    supervisor = Supervisor(tolerancia_segundos=10)
    tarea = supervisor.lanzar("tarea_de_prueba", revienta())
    await asyncio.gather(tarea, return_exceptions=True)
    # El done_callback corre en el siguiente ciclo del bucle de eventos.
    await asyncio.sleep(0)

    informe = supervisor.informe()
    fila = informe.tarea("tarea_de_prueba")

    assert informe.estado is EstadoSupervision.DEGRADADO
    assert fila.estado is EstadoTarea.FALLIDA
    assert fila.ultimo_error is not None
    assert "RuntimeError" in fila.ultimo_error.mensaje
    assert "se cayo el disparador" in fila.ultimo_error.mensaje


@pytest.mark.asyncio
async def prueba_una_tarea_viva_que_deja_de_latir_queda_retrasada() -> None:
    """La falla peor: la tarea existe, nadie la mato, y no procesa nada.

    Es el caso que un `/salud` normal no puede ver y que `/estado` tiene que ver.
    """
    supervisor = Supervisor(tolerancia_segundos=TOLERANCIA_CORTA)
    supervisor.lanzar("tarea_de_prueba", _nunca_termina())
    supervisor.latir("tarea_de_prueba")

    await asyncio.sleep(TOLERANCIA_CORTA * 3)

    informe = supervisor.informe()
    assert informe.tarea("tarea_de_prueba").estado is EstadoTarea.RETRASADA
    assert informe.estado is EstadoSupervision.DEGRADADO

    await supervisor.detener()


@pytest.mark.asyncio
async def prueba_un_error_anotado_no_degrada_por_si_solo() -> None:
    """Un corte transitorio del que la tarea se recupero no puede quedar en rojo.

    Lo que degrada es dejar de procesar, no haber tenido un error. Si un fallo puntual
    dejara el sistema degradado para siempre, `/estado` seria una luz roja permanente
    y se aprenderia a ignorarla.
    """
    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar("tarea_de_prueba", _nunca_termina())
    supervisor.anotar_error("tarea_de_prueba", ConnectionError("se cayo un instante"))
    supervisor.latir("tarea_de_prueba")

    informe = supervisor.informe()

    assert informe.estado is EstadoSupervision.OK
    assert informe.tarea("tarea_de_prueba").ultimo_error is not None

    await supervisor.detener()


def prueba_sin_tareas_registradas_no_hay_supervision() -> None:
    """Decir `ok` sin una sola tarea seria mentir; es un estado propio."""
    supervisor = Supervisor(tolerancia_segundos=10)

    informe = supervisor.informe()

    assert informe.estado is EstadoSupervision.SIN_SUPERVISION


def prueba_los_disparadores_previstos_se_reportan_sin_degradar() -> None:
    """D1, D2 y D3 se reportan como `no_construida` con su historia.

    Es la forma honesta de cumplir "el estado reporta D1/D2/D3" cuando todavia no
    existen: se dice que no existen y quien los construye. Un disparador simulado
    diria `corriendo` sin hacer nada, que es la mentira que esta ruta viene a evitar.
    """
    supervisor = Supervisor(tolerancia_segundos=10)

    informe = supervisor.informe()
    filas = {fila.nombre: fila for fila in informe.tareas}

    assert set(DISPARADORES_PREVISTOS) <= set(filas)
    for nombre, historia in DISPARADORES_PREVISTOS.items():
        assert filas[nombre].estado is EstadoTarea.NO_CONSTRUIDA
        assert filas[nombre].historia == historia

    # Y no arrastran el resumen: lo que falta por construir no es una falla.
    assert informe.estado is EstadoSupervision.SIN_SUPERVISION


@pytest.mark.asyncio
async def prueba_un_disparador_registrado_deja_de_ser_previsto() -> None:
    """El dia que H-09 lance `d1_deteccion`, se vigila como cualquier otra tarea."""
    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar("d1_deteccion", _nunca_termina())

    informe = supervisor.informe()
    fila = informe.tarea("d1_deteccion")

    assert fila.estado is EstadoTarea.CORRIENDO
    assert fila.historia == "H-09", "la fila conserva de que historia vino"
    assert informe.estado is EstadoSupervision.OK
    # Y aparece una sola vez: no se duplica con la fila del catalogo.
    assert [fila.nombre for fila in informe.tareas].count("d1_deteccion") == 1

    await supervisor.detener()


@pytest.mark.asyncio
async def prueba_no_se_registra_dos_veces_el_mismo_nombre() -> None:
    """Dos tareas con el mismo nombre serian dos disparadores iguales corriendo."""
    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar("tarea_de_prueba", _nunca_termina())

    with pytest.raises(ValueError):
        supervisor.lanzar("tarea_de_prueba", _nunca_termina())

    await supervisor.detener()


def prueba_una_tolerancia_no_positiva_no_se_acepta() -> None:
    with pytest.raises(ValueError):
        Supervisor(tolerancia_segundos=0)


# ---------------------------------------------------------------------------
# El latido
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def prueba_el_latido_late_antes_de_esperar() -> None:
    """Comprueba primero y duerme despues: el primer latido es al arrancar.

    Si fuera al contrario, `/estado` arrancaria sin un solo evento procesado durante
    todo el primer intervalo y habria que explicar por que.
    """
    comprobaciones = 0

    async def comprobar() -> None:
        nonlocal comprobaciones
        comprobaciones += 1

    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar(
        NOMBRE_LATIDO, latido(comprobar, supervisor, intervalo_segundos=3600)
    )
    await asyncio.sleep(0.05)

    fila = supervisor.informe().tarea(NOMBRE_LATIDO)
    assert comprobaciones == 1
    assert fila.eventos_procesados == 1
    assert fila.estado is EstadoTarea.CORRIENDO

    await supervisor.detener()


@pytest.mark.asyncio
async def prueba_el_latido_sobrevive_a_un_error_de_la_base() -> None:
    """Un fallo de la base no mata el latido: se anota y se vuelve a intentar.

    Y cuando la base vuelve, el latido vuelve a latir solo. Es la diferencia entre una
    tarea de fondo y un script.
    """
    intentos = 0

    async def comprobar() -> None:
        nonlocal intentos
        intentos += 1
        if intentos == 1:
            raise ConnectionError("la base no responde")

    supervisor = Supervisor(tolerancia_segundos=10)
    supervisor.lanzar(
        NOMBRE_LATIDO, latido(comprobar, supervisor, intervalo_segundos=0.01)
    )
    await asyncio.sleep(0.08)

    fila = supervisor.informe().tarea(NOMBRE_LATIDO)
    assert fila.estado is EstadoTarea.CORRIENDO, "el error no mato la tarea"
    assert fila.eventos_procesados >= 1, "volvio a latir cuando la base contesto"
    assert fila.ultimo_error is not None
    assert "ConnectionError" in fila.ultimo_error.mensaje

    await supervisor.detener()


@pytest.mark.asyncio
async def prueba_detener_cancela_todas_las_tareas() -> None:
    """Lo que hace el lifespan al apagar. Sin esto el proceso se queda colgado."""
    supervisor = Supervisor(tolerancia_segundos=10)
    primera = supervisor.lanzar("una", _nunca_termina())
    segunda = supervisor.lanzar("otra", _nunca_termina())

    await supervisor.detener()

    assert primera.cancelled() and segunda.cancelled()
    assert supervisor.informe().estado is EstadoSupervision.DEGRADADO
