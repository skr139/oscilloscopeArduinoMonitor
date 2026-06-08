"""
serial_manager.py — Comunicación serial en hilo independiente.

Lee el puerto serial en un QThread para no bloquear la interfaz gráfica.
Analiza líneas con formato:  ADC:<valor>\\tVoltios:<valor>
y emite señales Qt con los datos parseados y el estado de conexión.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional

import serial
import serial.tools.list_ports
from PySide6.QtCore import QObject, QThread, Signal, Slot

from signals_config import ADC_MAX, ADC_SCALE_FACTOR, SIGNAL_ADC, SIGNAL_VOLTIOS

# Solo acepta el formato exacto del firmware:  ADC:5.9\tVoltios:0.401
_STRICT_DATA_PATTERN = re.compile(
    r"^ADC:([+\-]?\d*\.?\d+(?:[eE][+\-]?\d+)?)"
    r"[\t ]+"
    r"Voltios:([+\-]?\d*\.?\d+(?:[eE][+\-]?\d+)?)\s*$"
)


def list_serial_ports() -> List[str]:
    """Detecta y devuelve los puertos seriales disponibles en el sistema."""
    ports = serial.tools.list_ports.comports()
    return [p.device for p in sorted(ports, key=lambda p: p.device)]


class SerialReaderThread(QThread):
    """
    Hilo dedicado a la lectura continua del puerto serial.

    Emite cada línea recibida y detecta desconexiones o errores de comunicación.
    """

    line_received = Signal(str, float)   # (línea cruda, timestamp recepción)
    error_occurred = Signal(str)
    connection_lost = Signal()

    def __init__(self, port: serial.Serial) -> None:
        super().__init__()
        self._port = port
        self._running = True

    def run(self) -> None:
        buffer = ""
        while self._running:
            try:
                # Leer bytes disponibles; si no hay, leer 1 byte con timeout del puerto.
                waiting = self._port.in_waiting
                raw = self._port.read(waiting if waiting > 0 else 1)
                if not raw:
                    continue

                buffer += raw.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip().rstrip("\r")
                    if line:
                        self.line_received.emit(line, time.time())

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
        self.wait(3000)


def parse_data_line(line: str) -> Optional[Dict[str, float]]:
    """
    Analiza una línea de datos del Arduino.

    Formato estricto esperado:
      ADC:5.9\\tVoltios:0.401

    El ADC en serie viene como adc/100 (ej: 5.1 → ADC 510).
    Líneas corruptas o con nombres distintos se descartan.

    :returns: Diccionario {ADC, Voltios} o None si no es válida.
    """
    if not line or line.startswith("#") or line.startswith("ACK_") or line.startswith("ERR_"):
        return None

    match = _STRICT_DATA_PATTERN.match(line.strip().rstrip("\r"))
    if not match:
        return None

    try:
        adc_scaled = float(match.group(1))
        voltios = float(match.group(2))
    except ValueError:
        return None

    # Reconstruir ADC real en rango 0–1023 a partir de adc/100 enviado por Arduino.
    adc_raw = max(0.0, min(ADC_MAX, adc_scaled * ADC_SCALE_FACTOR))

    return {SIGNAL_ADC: adc_raw, SIGNAL_VOLTIOS: voltios}


class SerialManager(QObject):
    """
    Gestor de alto nivel para la comunicación serial.

    Coordina la apertura/cierre del puerto, el hilo de lectura y el envío
    de comandos de texto al Arduino.
    """

    data_received = Signal(dict, float)      # (valores parseados, timestamp)
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

            # Esperar reset del Arduino tras apertura del puerto (DTR) y limpiar buffers.
            time.sleep(0.3)
            try:
                self._port.reset_input_buffer()
                self._port.reset_output_buffer()
            except serial.SerialException:
                pass

            self._reader = SerialReaderThread(self._port)
            self._reader.line_received.connect(self._on_line)
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
        """
        Libera completamente un puerto serial (especialmente en Windows).

        Cierra buffers, desactiva líneas de control y fuerza el cierre del handle
        para que otros programas (p. ej. Arduino IDE) puedan usar el COM.
        """
        if port is None:
            return

        try:
            if port.is_open:
                try:
                    port.reset_input_buffer()
                    port.reset_output_buffer()
                except serial.SerialException:
                    pass

                # Desactivar DTR/RTS para no mantener el dispositivo ocupado.
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
                # Detener el hilo; no desconectar señales Qt manualmente
                # porque eso puede impedir que la próxima conexión reciba datos.
                reader.stop()

            # Cerrar el puerto tras detener el hilo para liberar el COM.
            self._release_port(port)
        finally:
            self._disconnecting = False

        self.connection_changed.emit(False)
        self.status_changed.emit("Desconectado — puerto liberado")

    @Slot(str)
    def send_command(self, command: str) -> bool:
        """
        Envía un comando de texto al Arduino (añade \\n si falta).

        :returns: True si el envío fue exitoso.
        """
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
        """Procesa cada línea recibida del hilo de lectura."""
        self.raw_line_received.emit(line)

        parsed = parse_data_line(line)
        if parsed is not None:
            self.data_received.emit(parsed, timestamp)

    def _on_connection_lost(self) -> None:
        self.disconnect()
        self.status_changed.emit("Conexión perdida")

    def reopen(self, port_name: str, baudrate: int) -> bool:
        """Cierra y reabre el puerto (útil tras cambio de baudrate)."""
        self.disconnect()
        time.sleep(0.3)  # Esperar a que Arduino reinicie su UART
        return self.connect(port_name, baudrate)
