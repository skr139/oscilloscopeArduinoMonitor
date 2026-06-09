"""
serial_manager.py — Comunicación serial en hilo independiente.

Lee el puerto serial en un QThread para no bloquear la interfaz gráfica.
El Arduino envía solo el valor ADC (0–1023) por línea.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

import serial
import serial.tools.list_ports
from PySide6.QtCore import QObject, QThread, Signal, Slot

from signals_config import ADC_MAX, SIGNAL_ADC

# Formatos aceptados: "512" o "ADC:512"
_ADC_LINE_PATTERN = re.compile(r"^(?:ADC:)?(\d{1,4})\s*$", re.IGNORECASE)


def list_serial_ports() -> List[str]:
    """Detecta y devuelve los puertos seriales disponibles en el sistema."""
    ports = serial.tools.list_ports.comports()
    return [p.device for p in sorted(ports, key=lambda p: p.device)]


class SerialReaderThread(QThread):
    """
    Hilo dedicado a la lectura continua del puerto serial.

    Agrupa lecturas ADC en lotes para no saturar la cola de eventos de Qt
    (un Signal por línea a 500000 baud congela la interfaz).
    """

    line_received = Signal(str, float)   # Solo protocolo / mensajes no-ADC
    samples_batch_received = Signal(list)  # [(adc, timestamp), ...]
    error_occurred = Signal(str)
    connection_lost = Signal()

    def __init__(self, port: serial.Serial) -> None:
        super().__init__()
        self._port = port
        self._running = True
        self._pending_batch: list = []
        self._last_batch_emit = time.time()

    def _emit_batch_if_ready(self, force: bool = False) -> None:
        from performance_config import SERIAL_BATCH_INTERVAL_S, SERIAL_BATCH_MAX_SAMPLES

        if not self._pending_batch:
            return

        now = time.time()
        if not force and len(self._pending_batch) < SERIAL_BATCH_MAX_SAMPLES:
            if now - self._last_batch_emit < SERIAL_BATCH_INTERVAL_S:
                return

        self.samples_batch_received.emit(self._pending_batch)
        self._pending_batch = []
        self._last_batch_emit = now

    def run(self) -> None:
        buffer = ""
        while self._running:
            try:
                waiting = self._port.in_waiting
                raw = self._port.read(waiting if waiting > 0 else 1)
                if not raw:
                    self._emit_batch_if_ready()
                    self.msleep(1)
                    continue

                buffer += raw.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip().rstrip("\r")
                    if not line:
                        continue

                    ts = time.time()
                    parsed = parse_data_line(line)
                    if parsed is not None:
                        self._pending_batch.append((parsed[SIGNAL_ADC], ts))
                        self._emit_batch_if_ready()
                    else:
                        # Protocolo / depuración: una línea a la vez.
                        self.line_received.emit(line, ts)

            except serial.SerialException as exc:
                if self._running:
                    self.error_occurred.emit(f"Error de lectura: {exc}")
                    self.connection_lost.emit()
                break
            except Exception as exc:
                if self._running:
                    self.error_occurred.emit(f"Error inesperado: {exc}")
                break

    def stop(self) -> None:
        """Detiene el hilo de lectura y espera a que finalice."""
        self._running = False
        self._emit_batch_if_ready(force=True)
        self.wait(3000)


def parse_data_line(line: str) -> Optional[Dict[str, float]]:
    """
    Analiza una línea con el valor ADC enviado por Arduino.

    Formatos aceptados:
      512
      ADC:512

    :returns: Diccionario {ADC: valor} o None si la línea no es válida.
    """
    if not line or line.startswith("#") or line.startswith("ACK_") or line.startswith("ERR_"):
        return None

    match = _ADC_LINE_PATTERN.match(line.strip().rstrip("\r"))
    if not match:
        return None

    try:
        adc = int(match.group(1))
    except ValueError:
        return None

    if adc < 0 or adc > int(ADC_MAX):
        return None

    return {SIGNAL_ADC: float(adc)}


class SerialManager(QObject):
    """
    Gestor de alto nivel para la comunicación serial.

    Coordina la apertura/cierre del puerto, el hilo de lectura y el envío
    de comandos de texto al Arduino.
    """

    data_received = Signal(dict, float)      # Una muestra (legado)
    data_batch_received = Signal(list)       # [(adc, timestamp), ...]
    raw_line_received = Signal(str)          # Para la consola de depuración
    status_changed = Signal(str)             # Mensajes de estado
    connection_changed = Signal(bool)        # True = conectado
    error_occurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._port: Optional[serial.Serial] = None
        self._reader: Optional[SerialReaderThread] = None
        self._baudrate = 500_000
        self._port_name = ""
        self._disconnecting = False

    @property
    def is_connected(self) -> bool:
        return self._port is not None and self._port.is_open

    @property
    def baudrate(self) -> int:
        return self._baudrate

    @property
    def port_name(self) -> str:
        return self._port_name

    @Slot()
    def refresh_ports(self) -> List[str]:
        return list_serial_ports()

    @Slot(str, int)
    def connect(self, port_name: str, baudrate: int) -> bool:
        """
        Abre el puerto serial e inicia el hilo de lectura.

        :returns: True si la conexión fue exitosa.
        """
        if self.is_connected:
            self.disconnect()

        try:
            self._port = serial.Serial(
                port=port_name,
                baudrate=baudrate,
                timeout=0.1,
                write_timeout=2.0,
            )
            self._baudrate = baudrate
            self._port_name = port_name

            time.sleep(0.3)
            try:
                self._port.reset_input_buffer()
                self._port.reset_output_buffer()
            except serial.SerialException:
                pass

            self._reader = SerialReaderThread(self._port)
            self._reader.line_received.connect(self._on_line)
            self._reader.samples_batch_received.connect(self._on_samples_batch)
            self._reader.error_occurred.connect(self.error_occurred)
            self._reader.connection_lost.connect(self._on_connection_lost)
            self._reader.start()

            self.connection_changed.emit(True)
            self.status_changed.emit(f"Conectado a {port_name} @ {baudrate} baud")
            return True

        except serial.SerialException as exc:
            self.error_occurred.emit(f"No se pudo conectar: {exc}")
            self._port = None
            return False

    def _release_port(self, port: Optional[serial.Serial]) -> None:
        """Libera completamente un puerto serial."""
        if port is None:
            return

        try:
            if port.is_open:
                try:
                    port.reset_input_buffer()
                    port.reset_output_buffer()
                except serial.SerialException:
                    pass
                try:
                    port.dtr = False
                    port.rts = False
                except serial.SerialException:
                    pass
                port.close()
        except serial.SerialException:
            pass

    @Slot()
    def disconnect(self) -> None:
        """Cierra el puerto y detiene el hilo de lectura."""
        if self._disconnecting:
            return
        if not self.is_connected and self._reader is None:
            return

        self._disconnecting = True
        reader = self._reader
        port = self._port
        self._reader = None
        self._port = None

        try:
            if reader is not None:
                reader.stop()
            self._release_port(port)
        finally:
            self._disconnecting = False

        self.connection_changed.emit(False)
        self.status_changed.emit("Desconectado — puerto liberado")

    @Slot(str)
    def send_command(self, command: str) -> bool:
        """Envía un comando de texto al Arduino (añade \\n si falta)."""
        if not self.is_connected or self._port is None:
            self.error_occurred.emit("No hay conexión activa para enviar comandos")
            return False

        if not command.endswith("\n"):
            command += "\n"

        try:
            self._port.write(command.encode("utf-8"))
            self._port.flush()
            self.status_changed.emit(f"Enviado: {command.strip()}")
            return True
        except serial.SerialException as exc:
            self.error_occurred.emit(f"Error al enviar: {exc}")
            return False

    def _on_line(self, line: str, timestamp: float) -> None:
        """Procesa líneas de protocolo (no datos ADC)."""
        self.raw_line_received.emit(line)

    def _on_samples_batch(self, samples: list) -> None:
        """Reenvía un lote de muestras ADC a la interfaz (un Signal por lote)."""
        if samples:
            self.data_batch_received.emit(samples)

    def _on_connection_lost(self) -> None:
        self.disconnect()
        self.status_changed.emit("Conexión perdida")

    def reopen(self, port_name: str, baudrate: int) -> bool:
        """Cierra y reabre el puerto (útil tras cambio de baudrate)."""
        self.disconnect()
        time.sleep(0.3)
        return self.connect(port_name, baudrate)
