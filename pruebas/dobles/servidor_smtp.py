"""Servidor SMTP de prueba en proceso, solo biblioteca estandar (T-77).

Recibe correos de verdad por un socket real: lo que lo distingue de un doble de `enviar`
es que `smtplib` (el cliente que usa D2) habla con el de punta a punta. Para las pruebas
que no necesitan un Mailpit en Docker: corre en el CI sin servicios extra y en cualquier
maquina. Las pruebas que verifican contra Mailpit de verdad viven aparte
(`pruebas/integracion/prueba_d2_mailpit.py`).

No implementa SMTP completo: EHLO, MAIL, RCPT, DATA, RSET, NOOP y QUIT, lo que usa
`smtplib.SMTP.send_message`. `rechazar=True` responde 554 a MAIL para simular un servidor
que rechaza el correo (distinto del puerto cerrado, que es un `ConnectionRefusedError`).
"""

from __future__ import annotations

import socketserver
import threading
from email import message_from_bytes, policy
from email.message import Message
from typing import Self


class _Manejador(socketserver.StreamRequestHandler):
    server: ServidorSmtp

    def _responder(self, texto: str) -> None:
        self.wfile.write(texto.encode("ascii") + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        self._responder("220 prueba ESMTP")
        remitente = ""
        destinatarios: list[str] = []
        while True:
            linea = self.rfile.readline()
            if not linea:
                return
            comando = linea.decode("ascii", "replace").strip()
            verbo = comando.split(" ", 1)[0].upper()
            if verbo in ("EHLO", "HELO"):
                self._responder("250 prueba")
            elif verbo == "MAIL":
                if self.server.rechazar:
                    self._responder("554 rechazado por la prueba")
                    continue
                remitente = comando.partition(":")[2].strip()
                destinatarios = []
                self._responder("250 ok")
            elif verbo == "RCPT":
                destinatarios.append(comando.partition(":")[2].strip())
                self._responder("250 ok")
            elif verbo == "DATA":
                self._responder("354 adelante")
                cuerpo = bytearray()
                while True:
                    renglon = self.rfile.readline()
                    if renglon in (b".\r\n", b""):
                        break
                    # Quita el "dot stuffing" del protocolo.
                    cuerpo += renglon[1:] if renglon.startswith(b"..") else renglon
                mensaje = message_from_bytes(bytes(cuerpo), policy=policy.default)
                with self.server.candado:
                    self.server.recibidos.append((remitente, tuple(destinatarios), mensaje))
                self._responder("250 aceptado")
            elif verbo in ("RSET", "NOOP"):
                self._responder("250 ok")
            elif verbo == "QUIT":
                self._responder("221 adios")
                return
            else:
                self._responder("502 no implementado")


class ServidorSmtp(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *, rechazar: bool = False) -> None:
        super().__init__(("127.0.0.1", 0), _Manejador)
        self.rechazar = rechazar
        self.candado = threading.Lock()
        self.recibidos: list[tuple[str, tuple[str, ...], Message]] = []
        self._hilo = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def puerto(self) -> int:
        return int(self.server_address[1])

    def mensajes(self) -> list[Message]:
        with self.candado:
            return [mensaje for _, _, mensaje in self.recibidos]

    def arrancar(self) -> Self:
        self._hilo.start()
        return self

    def detener(self) -> None:
        self.shutdown()
        self.server_close()

    def __enter__(self) -> Self:
        return self.arrancar()

    def __exit__(self, *_: object) -> None:
        self.detener()
