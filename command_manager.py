"""
command_manager.py — Protocolo de comandos bidireccional con Arduino.

Gestiona el envío de comandos de texto y el protocolo de cambio de baudrate:

  1. La PC envía:   SET_BAUD=<nuevo_baudrate>
  2. Arduino cambia Serial.begin(nuevo_baudrate) y responde: ACK_BAUD=<nuevo_baudrate>
  3. La PC cierra el puerto, espera y se reconecta al nuevo baudrate.

IMPORTANTE: Arduino NO puede cambiar su velocidad solo porque la PC cambie el
baudrate del puerto. Ambos lados deben acordar el cambio mediante este protocolo.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

# Respuesta esperada del Arduino tras cambio de baudrate.
_ACK_PATTERN = re.compile(r"ACK_BAUD=(\d+)")
_ERR_PATTERN = re.compile(r"ERR_BAUD=(.+)")


# Baudrates estándar soportados por la interfaz.
STANDARD_BAUDRATES = [
    9600, 19200, 38400, 57600, 115200,
    230400, 250000, 500000, 1000000,
]


class CommandManager(QObject):
    """
    Gestor de comandos serial con soporte para cambio de baudrate coordinado.

    Se conecta al SerialManager para enviar comandos y escuchar respuestas ACK/ERR.
    """

    baudrate_change_started = Signal(int)       # Nuevo baudrate solicitado
    baudrate_change_completed = Signal(int)     # Cambio exitoso
    baudrate_change_failed = Signal(str)        # Error en el cambio
    command_sent = Signal(str)
    protocol_message = Signal(str)              # Mensajes del protocolo para la consola

    def __init__(self) -> None:
        super().__init__()
        self._pending_baudrate: Optional[int] = None
        self._waiting_ack = False
        self._reconnect_callback: Optional[Callable[[str, int], bool]] = None
        self._port_name = ""
        self._current_baudrate = 500_000
        self._ack_timer: Optional[QTimer] = None

    def configure(
        self,
        reconnect_fn: Callable[[str, int], bool],
        port_name: str,
        current_baudrate: int,
    ) -> None:
        """
        Configura las referencias necesarias para el cambio de baudrate.

        :param reconnect_fn: Función que cierra y reabre el puerto al nuevo baudrate.
        :param port_name: Puerto COM actual.
        :param current_baudrate: Baudrate actual de la conexión.
        """
        self._reconnect_callback = reconnect_fn
        self._port_name = port_name
        self._current_baudrate = current_baudrate

    def send_raw_command(self, send_fn: Callable[[str], bool], command: str) -> bool:
        """Envía un comando de texto arbitrario al Arduino."""
        success = send_fn(command)
        if success:
            self.command_sent.emit(command)
        return success

    def request_baudrate_change(
        self,
        send_fn: Callable[[str], bool],
        new_baudrate: int,
        port_name: str,
        current_baudrate: int,
    ) -> None:
        """
        Inicia el protocolo de cambio de baudrate.

        Secuencia:
          1. Enviar SET_BAUD=<new> al baudrate ACTUAL.
          2. Esperar ACK_BAUD=<new> del Arduino (timeout 3 s).
          3. Cerrar puerto y reconectar al nuevo baudrate.

        :param send_fn: Función de envío del SerialManager.
        :param new_baudrate: Velocidad deseada.
        :param port_name: Puerto COM activo.
        :param current_baudrate: Baudrate con el que está conectado ahora.
        """
        if new_baudrate == current_baudrate:
            self.protocol_message.emit("El baudrate ya está configurado en ese valor.")
            return

        if new_baudrate not in STANDARD_BAUDRATES:
            self.baudrate_change_failed.emit(
                f"Baudrate {new_baudrate} no está en la lista de valores estándar."
            )
            return

        self._pending_baudrate = new_baudrate
        self._port_name = port_name
        self._current_baudrate = current_baudrate
        self._waiting_ack = True

        self.baudrate_change_started.emit(new_baudrate)
        self.protocol_message.emit(
            f"[Protocolo] Enviando SET_BAUD={new_baudrate} @ {current_baudrate} baud..."
        )

        command = f"SET_BAUD={new_baudrate}"
        if not send_fn(command):
            self._waiting_ack = False
            self.baudrate_change_failed.emit("No se pudo enviar el comando SET_BAUD")
            return

        # Timeout: si Arduino no responde ACK en 3 s, fallar.
        if self._ack_timer is not None:
            self._ack_timer.stop()

        self._ack_timer = QTimer()
        self._ack_timer.setSingleShot(True)
        self._ack_timer.timeout.connect(self._on_ack_timeout)
        self._ack_timer.start(3000)

    def handle_response_line(self, line: str) -> bool:
        """
        Procesa líneas de protocolo recibidas del Arduino.

        :returns: True si la línea fue manejada por el protocolo (no es dato de sensor).
        """
        line = line.strip()

        if self._waiting_ack:
            ack_match = _ACK_PATTERN.match(line)
            if ack_match:
                ack_baud = int(ack_match.group(1))
                if self._ack_timer:
                    self._ack_timer.stop()
                self._waiting_ack = False
                self._complete_baudrate_change(ack_baud)
                return True

            err_match = _ERR_PATTERN.match(line)
            if err_match:
                if self._ack_timer:
                    self._ack_timer.stop()
                self._waiting_ack = False
                self.baudrate_change_failed.emit(f"Arduino reportó error: {err_match.group(1)}")
                return True

        return line.startswith("ACK_") or line.startswith("ERR_") or line.startswith("SET_")

    def _on_ack_timeout(self) -> None:
        self._waiting_ack = False
        self.baudrate_change_failed.emit(
            "Timeout: Arduino no respondió ACK_BAUD. "
            "Verifique que el firmware implementa el protocolo SET_BAUD."
        )

    def _complete_baudrate_change(self, ack_baud: int) -> None:
        """Cierra y reabre el puerto al nuevo baudrate tras recibir ACK."""
        self.protocol_message.emit(
            f"[Protocolo] ACK recibido. Reconectando a {ack_baud} baud..."
        )

        if self._reconnect_callback is None:
            self.baudrate_change_failed.emit("Callback de reconexión no configurado")
            return

        # Pausa no bloqueante: esperar a que Arduino reconfigure su UART.
        QTimer.singleShot(300, lambda: self._do_reconnect(ack_baud))

    def _do_reconnect(self, ack_baud: int) -> None:
        """Ejecuta la reconexión al nuevo baudrate (llamado tras delay)."""
        if self._reconnect_callback is None:
            return

        success = self._reconnect_callback(self._port_name, ack_baud)
        if success:
            self._current_baudrate = ack_baud
            self.baudrate_change_completed.emit(ack_baud)
            self.protocol_message.emit(
                f"[Protocolo] Reconectado exitosamente a {ack_baud} baud."
            )
        else:
            self.baudrate_change_failed.emit(
                f"No se pudo reconectar al baudrate {ack_baud}"
            )
