"""
main_window.py — Interfaz gráfica principal de la aplicación.

Integra todos los módulos (serial, buffer, plot, comandos) en una ventana
moderna con paneles de conexión, señales, estadísticas, controles de zoom,
grabación/exportación y consola serial.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from command_manager import STANDARD_BAUDRATES, CommandManager
from data_buffer import DataBuffer, SignalStats
from plot_manager import PlotManager
from serial_manager import SerialManager, list_serial_ports, parse_data_line
from median_filter import MedianFilter
from performance_config import (
    MAX_PENDING_SAMPLES,
    MAX_SAMPLES_PER_FRAME,
    PLOT_MAX_POINTS,
    PLOT_TIMER_MS,
    STATS_TIMER_MS,
)
from signals_config import (
    DEFAULT_RIGHT_MARGIN,
    DEFAULT_VI,
    DEFAULT_VS,
    KNOWN_SIGNALS,
    SIGNAL_ADC,
    SIGNAL_LABELS,
    SIGNAL_VOLTIOS,
)
from voltage_converter import VoltageConverter


# Opciones predefinidas de ventana temporal (segundos).
TIME_PRESETS = {
    "1 s": 1,
    "5 s": 5,
    "10 s": 10,
    "30 s": 30,
    "1 min": 60,
    "5 min": 300,
}


class MainWindow(QMainWindow):
    """Ventana principal del osciloscopio serial."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Osciloscopio Arduino — Monitor Serial")
        self.setMinimumSize(1280, 800)
        self.resize(1400, 900)

        # Módulos centrales.
        self._voltage_converter = VoltageConverter()
        self._median_filter = MedianFilter(maxlen=9, enabled=False)
        self._serial = SerialManager()
        self._buffer = DataBuffer(
            converter=self._voltage_converter,
            median_filter=self._median_filter,
        )
        self._plot = PlotManager()
        self._plot.set_right_margin(DEFAULT_RIGHT_MARGIN)
        self._commands = CommandManager()

        # Checkboxes fijos para ADC y Voltios.
        self._signal_checkboxes: Dict[str, QCheckBox] = {}

        # Cola de muestras pendientes (evita bloquear la UI con cada línea serial).
        self._pending_samples: List[tuple[float, float]] = []
        self._max_pending_samples = MAX_PENDING_SAMPLES

        self._setup_ui()
        self._setup_fixed_signal_checkboxes()
        self._connect_signals()

        # Timer de actualización de gráfica (~30 FPS).
        self._plot_timer = QTimer()
        self._plot_timer.timeout.connect(self._refresh_plot)
        self._plot_timer.start(PLOT_TIMER_MS)

        # Timer de estadísticas (proceso secundario, más lento que la gráfica).
        self._stats_timer = QTimer()
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(STATS_TIMER_MS)

        self._refresh_port_list()

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # Panel izquierdo: conexión + controles.
        left_panel = QVBoxLayout()
        left_panel.setSpacing(6)
        left_panel.addWidget(self._build_connection_panel())
        left_panel.addWidget(self._build_time_panel())
        left_panel.addWidget(self._build_voltage_panel())
        left_panel.addWidget(self._build_y_panel())
        left_panel.addWidget(self._build_signals_panel())
        left_panel.addWidget(self._build_filter_panel())
        left_panel.addWidget(self._build_recording_panel())
        left_panel.addWidget(self._build_command_panel())
        left_panel.addStretch()

        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setFixedWidth(300)

        scroll = QScrollArea()
        scroll.setWidget(left_widget)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Panel central: gráfica.
        center_panel = QVBoxLayout()
        center_panel.addWidget(self._plot)

        # Panel derecho: vista, estadísticas + consola.
        right_panel = QVBoxLayout()
        right_panel.addWidget(self._build_view_panel())
        right_panel.addWidget(self._build_stats_panel())
        right_panel.addWidget(self._build_console_panel())

        right_widget = QWidget()
        right_widget.setLayout(right_panel)
        right_widget.setFixedWidth(320)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(scroll)

        center_widget = QWidget()
        center_widget.setLayout(center_panel)
        splitter.addWidget(center_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        main_layout.addWidget(splitter)

        # Barra de estado.
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Listo. Seleccione un puerto COM para conectar.")

    def _build_connection_panel(self) -> QGroupBox:
        group = QGroupBox("Conexión Serial")
        layout = QFormLayout(group)

        port_row = QHBoxLayout()
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(120)
        self._refresh_ports_btn = QPushButton("↻")
        self._refresh_ports_btn.setFixedWidth(30)
        self._refresh_ports_btn.setToolTip("Actualizar lista de puertos")
        port_row.addWidget(self._port_combo)
        port_row.addWidget(self._refresh_ports_btn)
        layout.addRow("Puerto:", port_row)

        self._baud_combo = QComboBox()
        for baud in STANDARD_BAUDRATES:
            self._baud_combo.addItem(str(baud), baud)
        # Seleccionar 500000 por defecto.
        idx = self._baud_combo.findData(500_000)
        if idx >= 0:
            self._baud_combo.setCurrentIndex(idx)
        layout.addRow("Baudrate:", self._baud_combo)

        btn_row = QHBoxLayout()
        self._connect_btn = QPushButton("Conectar")
        self._connect_btn.setStyleSheet("QPushButton { background: #2e7d32; color: white; }")
        self._disconnect_btn = QPushButton("Desconectar")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.setStyleSheet("QPushButton { background: #c62828; color: white; }")
        btn_row.addWidget(self._connect_btn)
        btn_row.addWidget(self._disconnect_btn)
        layout.addRow(btn_row)

        self._conn_status_label = QLabel("● Desconectado")
        self._conn_status_label.setStyleSheet("color: #ef5350; font-weight: bold;")
        layout.addRow(self._conn_status_label)

        return group

    def _build_time_panel(self) -> QGroupBox:
        group = QGroupBox("Eje X — Tiempo")
        layout = QVBoxLayout(group)

        preset_row = QHBoxLayout()
        self._time_preset_combo = QComboBox()
        for label in TIME_PRESETS:
            self._time_preset_combo.addItem(label, TIME_PRESETS[label])
        self._time_preset_combo.addItem("Personalizado", -1)
        self._time_preset_combo.setCurrentIndex(2)  # 10 s por defecto.
        preset_row.addWidget(QLabel("Ventana:"))
        preset_row.addWidget(self._time_preset_combo)
        layout.addLayout(preset_row)

        custom_row = QHBoxLayout()
        self._custom_time_spin = QDoubleSpinBox()
        self._custom_time_spin.setRange(0.1, 3600)
        self._custom_time_spin.setValue(10)
        self._custom_time_spin.setSuffix(" s")
        self._custom_time_spin.setEnabled(False)
        custom_row.addWidget(QLabel("Custom:"))
        custom_row.addWidget(self._custom_time_spin)
        layout.addLayout(custom_row)

        zoom_row = QHBoxLayout()
        self._zoom_x_in_btn = QPushButton("X Zoom +")
        self._zoom_x_out_btn = QPushButton("X Zoom −")
        zoom_row.addWidget(self._zoom_x_in_btn)
        zoom_row.addWidget(self._zoom_x_out_btn)
        layout.addLayout(zoom_row)

        pan_row = QHBoxLayout()
        self._pan_left_btn = QPushButton("◀ Pan")
        self._pan_live_btn = QPushButton("▶ Live")
        self._pan_right_btn = QPushButton("Pan ▶")
        pan_row.addWidget(self._pan_left_btn)
        pan_row.addWidget(self._pan_live_btn)
        pan_row.addWidget(self._pan_right_btn)
        layout.addLayout(pan_row)

        self._pan_offset_label = QLabel("Offset: 0.0 s")
        layout.addWidget(self._pan_offset_label)

        return group

    def _build_y_panel(self) -> QGroupBox:
        group = QGroupBox("Eje Y — Escala")
        layout = QVBoxLayout(group)

        y_zoom_row = QHBoxLayout()
        self._zoom_y_in_btn = QPushButton("Y Zoom +")
        self._zoom_y_out_btn = QPushButton("Y Zoom −")
        self._auto_y_btn = QPushButton("Auto Y")
        y_zoom_row.addWidget(self._zoom_y_in_btn)
        y_zoom_row.addWidget(self._zoom_y_out_btn)
        layout.addLayout(y_zoom_row)
        layout.addWidget(self._auto_y_btn)

        manual_row = QHBoxLayout()
        self._y_min_spin = QDoubleSpinBox()
        self._y_min_spin.setRange(-1e6, 1e6)
        self._y_min_spin.setDecimals(4)
        self._y_min_spin.setValue(-1)
        self._y_max_spin = QDoubleSpinBox()
        self._y_max_spin.setRange(-1e6, 1e6)
        self._y_max_spin.setDecimals(4)
        self._y_max_spin.setValue(1)
        manual_row.addWidget(QLabel("Min:"))
        manual_row.addWidget(self._y_min_spin)
        manual_row.addWidget(QLabel("Max:"))
        manual_row.addWidget(self._y_max_spin)
        layout.addLayout(manual_row)

        self._apply_y_btn = QPushButton("Aplicar escala manual")
        layout.addWidget(self._apply_y_btn)

        self._grid_check = QCheckBox("Mostrar cuadrícula")
        self._grid_check.setChecked(True)
        layout.addWidget(self._grid_check)

        return group

    def _build_voltage_panel(self) -> QGroupBox:
        """
        Panel de conversión ADC → Voltios.

        Aquí se configuran Vi, Vs y el centro ADC. Pulse 'Aplicar' para activar.
        """
        group = QGroupBox("Conversión ADC → Voltios")
        layout = QVBoxLayout(group)

        hint = QLabel(
            "El Arduino envía solo ADC (0–1023).\n"
            "Fórmula (archivo voltage_converter.py):\n"
            "V = Vi + (ADC × (Vs − Vi) / 1023)"
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()

        self._vi_spin = QDoubleSpinBox()
        self._vi_spin.setRange(-100.0, 100.0)
        self._vi_spin.setDecimals(3)
        self._vi_spin.setSuffix(" V")
        self._vi_spin.setValue(DEFAULT_VI)
        self._vi_spin.setToolTip("Vinferior (Vmin): voltaje cuando ADC = 0")
        form.addRow("Vi (Vmin):", self._vi_spin)

        self._vs_spin = QDoubleSpinBox()
        self._vs_spin.setRange(-100.0, 100.0)
        self._vs_spin.setDecimals(3)
        self._vs_spin.setSuffix(" V")
        self._vs_spin.setValue(DEFAULT_VS)
        self._vs_spin.setToolTip("Vsuperior (Vmax): voltaje cuando ADC = 1023")
        form.addRow("Vs (Vmax):", self._vs_spin)

        layout.addLayout(form)

        self._apply_voltage_btn = QPushButton("Aplicar conversión")
        self._apply_voltage_btn.setStyleSheet(
            "QPushButton { background: #1565c0; color: white; font-weight: bold; }"
        )
        layout.addWidget(self._apply_voltage_btn)

        self._voltage_formula_label = QLabel(self._voltage_converter.formula_description())
        self._voltage_formula_label.setStyleSheet("color: #69f0ae; font-size: 10px;")
        self._voltage_formula_label.setWordWrap(True)
        layout.addWidget(self._voltage_formula_label)

        return group

    def _build_view_panel(self) -> QGroupBox:
        """Panel derecho: margen de visualización en el eje temporal."""
        group = QGroupBox("Vista de la gráfica")
        layout = QVBoxLayout(group)

        hint = QLabel(
            "Margen derecho: espacio vacío después del último dato "
            "para ver cómo avanza la señal."
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        margin_row = QHBoxLayout()
        self._right_margin_spin = QDoubleSpinBox()
        self._right_margin_spin.setRange(0.0, 60.0)
        self._right_margin_spin.setDecimals(2)
        self._right_margin_spin.setSuffix(" s")
        self._right_margin_spin.setValue(DEFAULT_RIGHT_MARGIN)
        self._right_margin_spin.setToolTip("Segundos de espacio vacío a la derecha")
        self._apply_margin_btn = QPushButton("Aplicar")
        margin_row.addWidget(self._right_margin_spin)
        margin_row.addWidget(self._apply_margin_btn)
        layout.addLayout(margin_row)

        return group

    def _build_filter_panel(self) -> QGroupBox:
        """Panel del filtro mediana sobre el ADC."""
        group = QGroupBox("Filtro mediana (ruido)")
        layout = QVBoxLayout(group)

        hint = QLabel(
            "Suaviza el ADC antes de convertir a voltios.\n"
            "Use ventana impar (ej: 5, 9, 11)."
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        maxlen_row = QHBoxLayout()
        self._median_maxlen_spin = QSpinBox()
        self._median_maxlen_spin.setRange(3, 101)
        self._median_maxlen_spin.setSingleStep(2)
        self._median_maxlen_spin.setValue(9)
        self._median_maxlen_spin.setToolTip("Tamaño de ventana (impar recomendado)")
        maxlen_row.addWidget(QLabel("maxlen:"))
        maxlen_row.addWidget(self._median_maxlen_spin)
        layout.addLayout(maxlen_row)

        btn_row = QHBoxLayout()
        self._median_apply_btn = QPushButton("Aplicar filtro")
        self._median_remove_btn = QPushButton("Quitar filtro")
        self._median_remove_btn.setEnabled(False)
        btn_row.addWidget(self._median_apply_btn)
        btn_row.addWidget(self._median_remove_btn)
        layout.addLayout(btn_row)

        self._median_status_label = QLabel("Filtro: desactivado")
        self._median_status_label.setStyleSheet("color: #888;")
        layout.addWidget(self._median_status_label)

        return group

    def _build_signals_panel(self) -> QGroupBox:
        self._signals_group = QGroupBox("Señales visibles")
        layout = QVBoxLayout(self._signals_group)

        hint = QLabel("Marque qué curvas desea ver en la gráfica:")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        self._show_all_signals_btn = QPushButton("Todas")
        self._hide_all_signals_btn = QPushButton("Ninguna")
        self._show_all_signals_btn.setToolTip("Mostrar todas las señales")
        self._hide_all_signals_btn.setToolTip("Ocultar todas las señales")
        btn_row.addWidget(self._show_all_signals_btn)
        btn_row.addWidget(self._hide_all_signals_btn)
        layout.addLayout(btn_row)

        self._signals_layout = QVBoxLayout()
        layout.addLayout(self._signals_layout)

        return self._signals_group

    def _build_recording_panel(self) -> QGroupBox:
        group = QGroupBox("Grabación y Exportación")
        layout = QVBoxLayout(group)

        rec_row = QHBoxLayout()
        self._record_btn = QPushButton("● Grabar")
        self._record_btn.setStyleSheet("QPushButton { color: #ef5350; }")
        self._stop_record_btn = QPushButton("■ Detener")
        self._stop_record_btn.setEnabled(False)
        rec_row.addWidget(self._record_btn)
        rec_row.addWidget(self._stop_record_btn)
        layout.addLayout(rec_row)

        self._export_rec_btn = QPushButton("Exportar grabación CSV")
        self._export_all_btn = QPushButton("Exportar todo el historial CSV")
        self._clear_btn = QPushButton("Limpiar buffer")
        layout.addWidget(self._export_rec_btn)
        layout.addWidget(self._export_all_btn)
        layout.addWidget(self._clear_btn)

        self._record_status = QLabel("Grabación: detenida")
        layout.addWidget(self._record_status)

        return group

    def _build_command_panel(self) -> QGroupBox:
        group = QGroupBox("Comandos Serial")
        layout = QVBoxLayout(group)

        cmd_row = QHBoxLayout()
        self._cmd_input = QLineEdit()
        self._cmd_input.setPlaceholderText("Ej: SET_BAUD=115200")
        self._cmd_send_btn = QPushButton("Enviar")
        cmd_row.addWidget(self._cmd_input)
        cmd_row.addWidget(self._cmd_send_btn)
        layout.addLayout(cmd_row)

        baud_row = QHBoxLayout()
        self._new_baud_combo = QComboBox()
        for baud in STANDARD_BAUDRATES:
            self._new_baud_combo.addItem(str(baud), baud)
        self._change_baud_btn = QPushButton("Cambiar baudrate")
        self._change_baud_btn.setToolTip(
            "Envía SET_BAUD al Arduino y reconecta automáticamente"
        )
        baud_row.addWidget(self._new_baud_combo)
        baud_row.addWidget(self._change_baud_btn)
        layout.addLayout(baud_row)

        return group

    def _build_stats_panel(self) -> QGroupBox:
        group = QGroupBox("Estadísticas")
        layout = QVBoxLayout(group)

        self._sample_rate_label = QLabel("Frecuencia: — Hz")
        self._sample_count_label = QLabel("Muestras: 0")
        self._duration_label = QLabel("Duración: 0.0 s")
        layout.addWidget(self._sample_rate_label)
        layout.addWidget(self._sample_count_label)
        layout.addWidget(self._duration_label)

        self._stats_table = QTableWidget(0, 5)
        self._stats_table.setHorizontalHeaderLabels(
            ["Señal", "Actual", "Mín", "Máx", "Prom"]
        )
        self._stats_table.horizontalHeader().setStretchLastSection(True)
        self._stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._stats_table.setMaximumHeight(250)
        layout.addWidget(self._stats_table)

        return group

    def _build_console_panel(self) -> QGroupBox:
        group = QGroupBox("Consola Serial")
        layout = QVBoxLayout(group)

        self._console = QTextEdit()
        self._console.setReadOnly(True)
        self._console.setFont(QFont("Consolas", 9))
        self._console.setMaximumHeight(300)
        layout.addWidget(self._console)

        console_btn_row = QHBoxLayout()
        self._clear_console_btn = QPushButton("Limpiar consola")
        console_btn_row.addWidget(self._clear_console_btn)
        layout.addLayout(console_btn_row)

        return group

    # ------------------------------------------------------------------
    # Conexión de señales Qt
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        # Serial.
        self._refresh_ports_btn.clicked.connect(self._refresh_port_list)
        self._connect_btn.clicked.connect(self._on_connect)
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        self._serial.data_batch_received.connect(self._on_data_batch)
        self._serial.raw_line_received.connect(self._on_raw_line)
        self._serial.status_changed.connect(self._status_bar.showMessage)
        self._serial.connection_changed.connect(self._on_connection_changed)
        self._serial.error_occurred.connect(self._on_error)

        # Tiempo.
        self._time_preset_combo.currentIndexChanged.connect(self._on_time_preset_changed)
        self._custom_time_spin.valueChanged.connect(self._on_custom_time_changed)
        self._zoom_x_in_btn.clicked.connect(lambda: self._plot.zoom_x_in())
        self._zoom_x_out_btn.clicked.connect(lambda: self._plot.zoom_x_out())
        self._pan_left_btn.clicked.connect(lambda: self._plot.pan_left())
        self._pan_live_btn.clicked.connect(self._on_pan_live)
        self._pan_right_btn.clicked.connect(lambda: self._plot.pan_right())

        # Eje Y.
        self._zoom_y_in_btn.clicked.connect(lambda: self._plot.zoom_y_in())
        self._zoom_y_out_btn.clicked.connect(lambda: self._plot.zoom_y_out())
        self._auto_y_btn.clicked.connect(self._on_auto_y)
        self._apply_y_btn.clicked.connect(self._on_apply_y_manual)
        self._grid_check.toggled.connect(self._plot.set_grid_visible)

        # Conversión de voltaje.
        self._apply_voltage_btn.clicked.connect(self._on_apply_voltage_params)
        self._apply_margin_btn.clicked.connect(self._on_apply_right_margin)

        # Filtro mediana.
        self._median_apply_btn.clicked.connect(self._on_apply_median_filter)
        self._median_remove_btn.clicked.connect(self._on_remove_median_filter)

        # Señales.
        self._show_all_signals_btn.clicked.connect(self._on_show_all_signals)
        self._hide_all_signals_btn.clicked.connect(self._on_hide_all_signals)

        # Grabación.
        self._record_btn.clicked.connect(self._on_start_recording)
        self._stop_record_btn.clicked.connect(self._on_stop_recording)
        self._export_rec_btn.clicked.connect(self._on_export_recording)
        self._export_all_btn.clicked.connect(self._on_export_all)
        self._clear_btn.clicked.connect(self._on_clear_buffer)

        # Comandos.
        self._cmd_send_btn.clicked.connect(self._on_send_command)
        self._cmd_input.returnPressed.connect(self._on_send_command)
        self._change_baud_btn.clicked.connect(self._on_change_baudrate)

        # Protocolo de baudrate.
        self._commands.baudrate_change_completed.connect(self._on_baudrate_changed)
        self._commands.baudrate_change_failed.connect(self._on_baudrate_failed)
        self._commands.protocol_message.connect(self._append_console)

    # ------------------------------------------------------------------
    # Slots de conexión serial
    # ------------------------------------------------------------------

    def _refresh_port_list(self) -> None:
        ports = list_serial_ports()
        current = self._port_combo.currentText()
        self._port_combo.clear()
        self._port_combo.addItems(ports)
        idx = self._port_combo.findText(current)
        if idx >= 0:
            self._port_combo.setCurrentIndex(idx)

    def _on_connect(self) -> None:
        port = self._port_combo.currentText()
        if not port:
            QMessageBox.warning(self, "Sin puerto", "No hay puertos COM disponibles.")
            return
        baud = self._baud_combo.currentData()
        self._serial.connect(port, baud)
        self._commands.configure(
            reconnect_fn=self._serial.reopen,
            port_name=port,
            current_baudrate=baud,
        )

    def _on_disconnect(self) -> None:
        self._serial.disconnect()

    def _on_connection_changed(self, connected: bool) -> None:
        self._connect_btn.setEnabled(not connected)
        self._disconnect_btn.setEnabled(connected)
        self._port_combo.setEnabled(not connected)
        self._baud_combo.setEnabled(not connected)
        if connected:
            self._conn_status_label.setText("● Conectado")
            self._conn_status_label.setStyleSheet("color: #66bb6a; font-weight: bold;")
        else:
            self._conn_status_label.setText("● Desconectado")
            self._conn_status_label.setStyleSheet("color: #ef5350; font-weight: bold;")

    def _on_data_batch(self, samples: list) -> None:
        """Encola un lote de muestras ADC (un evento Qt por lote, no por línea)."""
        self._pending_samples.extend(samples)
        if len(self._pending_samples) > self._max_pending_samples:
            # Decimar: conservar las más recientes si el Arduino supera la capacidad.
            step = max(1, len(self._pending_samples) // self._max_pending_samples)
            self._pending_samples = self._pending_samples[::step][-self._max_pending_samples:]

    def _flush_pending_samples(self) -> None:
        """Vuelca hasta MAX_SAMPLES_PER_FRAME muestras por cuadro."""
        if not self._pending_samples:
            return
        batch = self._pending_samples[:MAX_SAMPLES_PER_FRAME]
        self._pending_samples = self._pending_samples[MAX_SAMPLES_PER_FRAME:]
        self._buffer.add_samples_batch(batch)

    def _on_raw_line(self, line: str) -> None:
        if self._commands.handle_response_line(line):
            self._append_console(f"[PROTO] {line}")
            return
        # No volcar cada lectura ADC a la consola: con alta frecuencia congela la UI.
        if parse_data_line(line) is not None:
            return
        self._append_console(line)

    def _on_error(self, message: str) -> None:
        self._status_bar.showMessage(f"Error: {message}")
        self._append_console(f"[ERROR] {message}")

    # ------------------------------------------------------------------
    # Conversión de voltaje y vista
    # ------------------------------------------------------------------

    def _on_apply_voltage_params(self) -> None:
        """Aplica Vi y Vs; recalcula todo el historial de Voltios."""
        vi = self._vi_spin.value()
        vs = self._vs_spin.value()

        if vi >= vs:
            QMessageBox.warning(
                self,
                "Parámetros inválidos",
                "Vi (Vmin) debe ser menor que Vs (Vmax).",
            )
            return

        self._voltage_converter.set_params(vi, vs)
        self._buffer.set_converter(self._voltage_converter)
        self._voltage_formula_label.setText(self._voltage_converter.formula_description())
        self._status_bar.showMessage(f"Conversión aplicada: Vi={vi} V, Vs={vs} V")
        self._refresh_plot()

    def _on_apply_median_filter(self) -> None:
        """Activa el filtro mediana con el maxlen indicado."""
        maxlen = self._median_maxlen_spin.value() | 1
        self._median_maxlen_spin.setValue(maxlen)
        self._buffer.set_median_filter(maxlen, enabled=True)
        self._median_apply_btn.setEnabled(False)
        self._median_remove_btn.setEnabled(True)
        self._median_maxlen_spin.setEnabled(False)
        self._median_status_label.setText(f"Filtro: ACTIVO (ventana={maxlen})")
        self._median_status_label.setStyleSheet("color: #69f0ae; font-weight: bold;")
        self._status_bar.showMessage(f"Filtro mediana activado (maxlen={maxlen})")

    def _on_remove_median_filter(self) -> None:
        """Desactiva el filtro mediana."""
        maxlen = self._median_maxlen_spin.value()
        self._buffer.set_median_filter(maxlen, enabled=False)
        self._median_apply_btn.setEnabled(True)
        self._median_remove_btn.setEnabled(False)
        self._median_maxlen_spin.setEnabled(True)
        self._median_status_label.setText("Filtro: desactivado")
        self._median_status_label.setStyleSheet("color: #888;")
        self._status_bar.showMessage("Filtro mediana desactivado")

    def _on_apply_right_margin(self) -> None:
        """Aplica el margen derecho de la gráfica."""
        margin = self._right_margin_spin.value()
        self._plot.set_right_margin(margin)
        self._status_bar.showMessage(f"Margen derecho: {margin:.2f} s")
        self._refresh_plot()

    # ------------------------------------------------------------------
    # Señales fijas: ADC y Voltios
    # ------------------------------------------------------------------

    def _setup_fixed_signal_checkboxes(self) -> None:
        """Crea los checkboxes para las dos únicas señales soportadas."""
        for name in KNOWN_SIGNALS:
            if name in self._signal_checkboxes:
                continue

            color = self._plot.get_signal_color(name)
            label = SIGNAL_LABELS.get(name, name)

            cb = QCheckBox(f"  {label}")
            cb.setChecked(True)
            cb.setToolTip(f"Mostrar u ocultar {label}")
            cb.setStyleSheet(
                f"QCheckBox {{ color: {color}; font-weight: bold; font-size: 13px; }}"
                f"QCheckBox::indicator:checked {{ background-color: {color}; border: 1px solid {color}; }}"
            )
            cb.toggled.connect(lambda checked, n=name: self._on_signal_toggled(n, checked))
            self._signals_layout.addWidget(cb)
            self._signal_checkboxes[name] = cb

    def _on_signal_toggled(self, name: str, enabled: bool) -> None:
        self._plot.set_signal_enabled(name, enabled)
        if self._plot.auto_y:
            self._plot.reset_y_auto()
        self._refresh_plot()

    def _on_show_all_signals(self) -> None:
        for cb in self._signal_checkboxes.values():
            cb.setChecked(True)

    def _on_hide_all_signals(self) -> None:
        for cb in self._signal_checkboxes.values():
            cb.setChecked(False)

    # ------------------------------------------------------------------
    # Controles de tiempo y eje Y
    # ------------------------------------------------------------------

    def _on_time_preset_changed(self, index: int) -> None:
        seconds = self._time_preset_combo.currentData()
        if seconds == -1:
            self._custom_time_spin.setEnabled(True)
            seconds = self._custom_time_spin.value()
        else:
            self._custom_time_spin.setEnabled(False)
        self._plot.set_window_seconds(seconds)

    def _on_custom_time_changed(self, value: float) -> None:
        if self._time_preset_combo.currentData() == -1:
            self._plot.set_window_seconds(value)

    def _on_pan_live(self) -> None:
        self._plot.pan_to_live()
        self._pan_offset_label.setText("Offset: 0.0 s")

    def _on_auto_y(self) -> None:
        self._plot.reset_y_auto()

    def _on_apply_y_manual(self) -> None:
        y_min = self._y_min_spin.value()
        y_max = self._y_max_spin.value()
        if y_min >= y_max:
            QMessageBox.warning(self, "Rango inválido", "El mínimo debe ser menor que el máximo.")
            return
        self._plot.set_manual_y_range(y_min, y_max)

    # ------------------------------------------------------------------
    # Actualización periódica de gráfica y estadísticas
    # ------------------------------------------------------------------

    def _refresh_plot(self) -> None:
        self._flush_pending_samples()

        enabled = [
            name for name, cb in self._signal_checkboxes.items() if cb.isChecked()
        ]
        ts, data = self._buffer.get_window(
            self._plot.window_seconds,
            self._plot.pan_offset,
            max_points=PLOT_MAX_POINTS,
        )
        # La escala Y se calcula con los datos YA obtenidos (los picos están
        # preservados por la decimación min/máx), evitando re-escanear el buffer.
        y_range = None
        if self._plot.auto_y:
            y_range = self._compute_y_range(data, enabled)

        self._update_y_axis_label(enabled)
        self._plot.update_plot(ts, data, y_range)

    def _compute_y_range(self, data: dict, enabled: List[str]):
        """Rango Y a partir de los datos visibles, sin volver a leer el buffer."""
        if not data:
            return None

        scale = list(enabled) if enabled else list(KNOWN_SIGNALS)
        # Si ADC y Voltios están juntos, escalar solo por Voltios
        # (el ADC 0–1023 distorsionaría el eje en la vista de voltaje).
        if enabled and SIGNAL_VOLTIOS in enabled and SIGNAL_ADC in enabled:
            scale = [SIGNAL_VOLTIOS]

        p = self._voltage_converter.params
        mins: List[float] = []
        maxs: List[float] = []
        for name in scale:
            arr = data.get(name)
            if arr is None or len(arr) == 0:
                continue
            valid = arr[~np.isnan(arr)]
            if len(valid) == 0:
                continue
            v_min = float(valid.min())
            v_max = float(valid.max())
            if name == SIGNAL_VOLTIOS:
                v_min = max(p.vi, v_min)
                v_max = min(p.vs, v_max)
            else:
                v_min = max(0.0, v_min)
                v_max = min(1023.0, v_max)
            mins.append(v_min)
            maxs.append(v_max)

        if not mins:
            return None

        y_min, y_max = min(mins), max(maxs)
        if y_min >= y_max:
            y_max = y_min + 0.1
        margin = (y_max - y_min) * 0.05 or 0.1
        return (y_min - margin, y_max + margin)

    def _update_y_axis_label(self, enabled: List[str]) -> None:
        """Etiqueta del eje Y acorde a la señal que se está viendo."""
        if enabled == [SIGNAL_VOLTIOS]:
            self._plot.set_y_label("Voltios", "V")
        elif enabled == [SIGNAL_ADC]:
            self._plot.set_y_label("ADC")
        elif SIGNAL_VOLTIOS in enabled and SIGNAL_ADC not in enabled:
            self._plot.set_y_label("Voltios", "V")
        elif SIGNAL_ADC in enabled and SIGNAL_VOLTIOS not in enabled:
            self._plot.set_y_label("ADC")
        else:
            self._plot.set_y_label("Valor")
        self._pan_offset_label.setText(f"Offset: {self._plot.pan_offset:.1f} s")

    def _refresh_stats(self) -> None:
        # No vaciar la cola aquí (lo hace el timer de gráfica) para no duplicar trabajo.
        stats = self._buffer.get_stats()
        rate = self._buffer.sample_rate_hz
        count = self._buffer.sample_count
        self._sample_rate_label.setText(f"Frecuencia: {rate:.1f} Hz")
        self._sample_count_label.setText(f"Muestras: {count:,}")
        self._duration_label.setText(f"Duración: {self._buffer.duration:.1f} s")
        if count > 0:
            self._status_bar.showMessage(
                f"Recibiendo datos — {count:,} muestras | {rate:.0f} Hz"
            )

        visible_stats = {
            name: s for name, s in stats.items()
            if name in self._signal_checkboxes and self._signal_checkboxes[name].isChecked()
        }
        if not visible_stats and stats:
            visible_stats = stats

        self._stats_table.setRowCount(len(visible_stats))
        for row, (name, s) in enumerate(visible_stats.items()):
            color = self._plot.get_signal_color(name)
            for col, text in enumerate([
                name,
                f"{s.current:.4f}" if s.count else "—",
                f"{s.minimum:.4f}" if s.count else "—",
                f"{s.maximum:.4f}" if s.count else "—",
                f"{s.average:.4f}" if s.count else "—",
            ]):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setForeground(QColor(color))
                self._stats_table.setItem(row, col, item)

    # ------------------------------------------------------------------
    # Grabación y exportación CSV
    # ------------------------------------------------------------------

    def _on_start_recording(self) -> None:
        self._buffer.start_recording()
        self._record_btn.setEnabled(False)
        self._stop_record_btn.setEnabled(True)
        self._record_status.setText("Grabación: ACTIVA")
        self._record_status.setStyleSheet("color: #ef5350; font-weight: bold;")

    def _on_stop_recording(self) -> None:
        self._buffer.stop_recording()
        self._record_btn.setEnabled(True)
        self._stop_record_btn.setEnabled(False)
        self._record_status.setText("Grabación: detenida")
        self._record_status.setStyleSheet("")

    def _on_export_recording(self) -> None:
        ts_list, signals = self._buffer.export_recording()
        if not ts_list:
            QMessageBox.information(self, "Sin datos", "No hay datos grabados para exportar.")
            return
        path = self._ask_csv_path("grabacion")
        if path:
            self._write_csv(path, ts_list, signals)

    def _on_export_all(self) -> None:
        if self._buffer.sample_count == 0:
            QMessageBox.information(self, "Sin datos", "No hay datos en el buffer.")
            return
        _, signals, abs_ts = self._buffer.export_all()
        path = self._ask_csv_path("historial")
        if path:
            self._write_csv(path, abs_ts, {k: v.tolist() for k, v in signals.items()})

    def _ask_csv_path(self, prefix: str) -> Optional[str]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"{prefix}_{timestamp}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar CSV", default_name, "CSV (*.csv)"
        )
        return path if path else None

    def _write_csv(
        self,
        path: str,
        timestamps: list,
        signals: Dict[str, list],
    ) -> None:
        """Escribe un archivo CSV con timestamp y columnas de señales."""
        signal_names = sorted(signals.keys())
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp"] + signal_names)
                n = len(timestamps)
                for i in range(n):
                    row = [timestamps[i]]
                    for name in signal_names:
                        vals = signals[name]
                        row.append(vals[i] if i < len(vals) else "")
                    writer.writerow(row)
            self._status_bar.showMessage(f"CSV guardado: {path}")
            self._append_console(f"[INFO] Exportado: {os.path.basename(path)}")
        except OSError as exc:
            QMessageBox.critical(self, "Error de escritura", str(exc))

    def _on_clear_buffer(self) -> None:
        reply = QMessageBox.question(
            self, "Confirmar", "¿Limpiar todo el buffer de datos?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._buffer.clear()
            self._plot.clear()
            for cb in self._signal_checkboxes.values():
                cb.setChecked(True)

    # ------------------------------------------------------------------
    # Comandos y cambio de baudrate
    # ------------------------------------------------------------------

    def _on_send_command(self) -> None:
        cmd = self._cmd_input.text().strip()
        if not cmd:
            return
        self._commands.send_raw_command(self._serial.send_command, cmd)
        self._cmd_input.clear()

    def _on_change_baudrate(self) -> None:
        if not self._serial.is_connected:
            QMessageBox.warning(self, "Sin conexión", "Conecte primero al puerto serial.")
            return
        new_baud = self._new_baud_combo.currentData()
        self._commands.request_baudrate_change(
            send_fn=self._serial.send_command,
            new_baudrate=new_baud,
            port_name=self._serial.port_name,
            current_baudrate=self._serial.baudrate,
        )

    def _on_baudrate_changed(self, new_baud: int) -> None:
        idx = self._baud_combo.findData(new_baud)
        if idx >= 0:
            self._baud_combo.setCurrentIndex(idx)
        self._status_bar.showMessage(f"Baudrate cambiado a {new_baud}")
        self._commands.configure(
            reconnect_fn=self._serial.reopen,
            port_name=self._serial.port_name,
            current_baudrate=new_baud,
        )

    def _on_baudrate_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Cambio de baudrate fallido", message)
        self._append_console(f"[ERROR] {message}")

    # ------------------------------------------------------------------
    # Consola
    # ------------------------------------------------------------------

    def _append_console(self, text: str) -> None:
        self._console.append(text)
        # Limitar tamaño de la consola para no consumir memoria.
        if self._console.document().blockCount() > 500:
            self._console.clear()

    def changeEvent(self, event) -> None:
        """Pausa actualizaciones al minimizar para evitar que la ventana se cuelgue."""
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._plot_timer.stop()
                self._stats_timer.stop()
            else:
                self._plot_timer.start(PLOT_TIMER_MS)
                self._stats_timer.start(STATS_TIMER_MS)
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        """Detiene timers y libera el puerto COM antes de cerrar."""
        self._plot_timer.stop()
        self._stats_timer.stop()
        self._flush_pending_samples()
        if self._serial.is_connected:
            self._serial.disconnect()
        event.accept()
