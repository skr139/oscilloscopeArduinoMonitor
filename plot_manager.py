"""
plot_manager.py — Visualización en tiempo real con PyQtGraph.

Gestiona la gráfica principal, curvas multicanal, zoom/pan en ejes X e Y,
autoescalado, cuadrícula configurable y ventana temporal desplazable.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout

from signals_config import KNOWN_SIGNALS

# Paleta de colores para señales (alta visibilidad sobre fondo oscuro).
SIGNAL_COLORS = [
    "#00E5FF",  # cian
    "#FF6D00",  # naranja
    "#76FF03",  # verde lima
    "#E040FB",  # magenta
    "#FFEA00",  # amarillo
    "#FF5252",  # rojo
    "#448AFF",  # azul
    "#69F0AE",  # verde agua
]


class PlotManager(QWidget):
    """
    Widget de gráfica con controles de zoom, pan y autoescalado.

    Se actualiza externamente mediante update_plot() llamado desde un QTimer
    para mantener la interfaz fluida sin bloquear el hilo principal.
    """

    range_changed = Signal(float, float)  # (x_min, x_max) tras pan/zoom manual

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._curves: Dict[str, pg.PlotDataItem] = {}
        self._signal_colors: Dict[str, str] = {}
        self._enabled_signals: Dict[str, bool] = {}

        # Estado de la ventana temporal.
        self._window_seconds = 10.0
        self._pan_offset = 0.0

        # Estado del eje Y.
        self._auto_y = True
        self._manual_y_min: Optional[float] = None
        self._manual_y_max: Optional[float] = None
        self._show_grid = True

        self._setup_ui()
        self.setup_fixed_signals()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Configurar estilo oscuro de PyQtGraph.
        pg.setConfigOptions(antialias=True, background="#1a1a2e", foreground="#e0e0e0")

        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setLabel("bottom", "Tiempo", units="s")
        self._plot_widget.setLabel("left", "Valor")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self._plot_widget.setMouseEnabled(x=True, y=True)

        # Eje X con escala temporal legible.
        self._plot_widget.getPlotItem().getAxis("bottom").enableAutoSIPrefix(False)

        layout.addWidget(self._plot_widget)

    @property
    def plot_widget(self) -> pg.PlotWidget:
        return self._plot_widget

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    @property
    def pan_offset(self) -> float:
        return self._pan_offset

    @property
    def auto_y(self) -> bool:
        return self._auto_y

    # ------------------------------------------------------------------
    # Gestión de señales / curvas
    # ------------------------------------------------------------------

    def setup_fixed_signals(self) -> None:
        """Registra únicamente las señales fijas ADC y Voltios."""
        for name in KNOWN_SIGNALS:
            self.register_signal(name)

    def register_signal(self, name: str) -> None:
        """Crea una curva para una señal conocida (solo ADC o Voltios)."""
        if name not in KNOWN_SIGNALS or name in self._curves:
            return

        color_idx = len(self._curves) % len(SIGNAL_COLORS)
        color = SIGNAL_COLORS[color_idx]
        self._signal_colors[name] = color
        self._enabled_signals[name] = True

        pen = pg.mkPen(color=color, width=2)
        curve = self._plot_widget.plot([], [], pen=pen, name=name)
        self._curves[name] = curve

    def set_signal_enabled(self, name: str, enabled: bool) -> None:
        """Activa o desactiva la visualización de una señal."""
        self._enabled_signals[name] = enabled
        if name in self._curves:
            curve = self._curves[name]
            curve.setVisible(enabled)
            if not enabled:
                # Borrar datos residuales para que la curva no siga visible.
                curve.setData([], [])

    def set_all_signals_enabled(self, enabled: bool) -> None:
        """Activa o desactiva todas las señales registradas."""
        for name in list(self._curves.keys()):
            self.set_signal_enabled(name, enabled)

    def get_enabled_signals(self) -> List[str]:
        """Devuelve los nombres de las señales actualmente visibles."""
        return [name for name, on in self._enabled_signals.items() if on]

    def get_signal_color(self, name: str) -> str:
        return self._signal_colors.get(name, "#FFFFFF")

    def signal_names(self) -> List[str]:
        return list(self._curves.keys())

    # ------------------------------------------------------------------
    # Control del eje X (tiempo)
    # ------------------------------------------------------------------

    def set_window_seconds(self, seconds: float) -> None:
        """Configura el ancho de la ventana temporal visible."""
        self._window_seconds = max(0.1, seconds)

    def set_pan_offset(self, offset: float) -> None:
        """Desplaza la vista hacia atrás en el historial (segundos desde el final)."""
        self._pan_offset = max(0.0, offset)

    def pan_left(self, fraction: float = 0.25) -> None:
        """Mueve la vista hacia el pasado."""
        self._pan_offset += self._window_seconds * fraction

    def pan_right(self, fraction: float = 0.25) -> None:
        """Mueve la vista hacia el presente."""
        self._pan_offset = max(0.0, self._pan_offset - self._window_seconds * fraction)

    def pan_to_live(self) -> None:
        """Vuelve a la vista en tiempo real (sin desplazamiento)."""
        self._pan_offset = 0.0

    def zoom_x_in(self, factor: float = 0.5) -> None:
        """Reduce la ventana temporal (zoom-in horizontal)."""
        self._window_seconds = max(0.1, self._window_seconds * factor)

    def zoom_x_out(self, factor: float = 2.0) -> None:
        """Amplía la ventana temporal (zoom-out horizontal)."""
        self._window_seconds = min(3600.0, self._window_seconds * factor)

    # ------------------------------------------------------------------
    # Control del eje Y
    # ------------------------------------------------------------------

    def set_auto_y(self, enabled: bool) -> None:
        self._auto_y = enabled

    def set_manual_y_range(self, y_min: float, y_max: float) -> None:
        self._manual_y_min = y_min
        self._manual_y_max = y_max
        self._auto_y = False
        self._plot_widget.setYRange(y_min, y_max, padding=0)

    def zoom_y_in(self, factor: float = 0.5) -> None:
        """Zoom-in vertical centrado en el rango actual."""
        y_range = self._plot_widget.viewRange()[1]
        center = (y_range[0] + y_range[1]) / 2
        half = (y_range[1] - y_range[0]) / 2 * factor
        self._auto_y = False
        self._manual_y_min = center - half
        self._manual_y_max = center + half
        self._plot_widget.setYRange(self._manual_y_min, self._manual_y_max, padding=0)

    def zoom_y_out(self, factor: float = 2.0) -> None:
        """Zoom-out vertical."""
        y_range = self._plot_widget.viewRange()[1]
        center = (y_range[0] + y_range[1]) / 2
        half = (y_range[1] - y_range[0]) / 2 * factor
        self._auto_y = False
        self._manual_y_min = center - half
        self._manual_y_max = center + half
        self._plot_widget.setYRange(self._manual_y_min, self._manual_y_max, padding=0)

    def reset_y_auto(self) -> None:
        """Vuelve al autoescalado del eje Y."""
        self._auto_y = True
        self._manual_y_min = None
        self._manual_y_max = None

    def set_grid_visible(self, visible: bool) -> None:
        self._show_grid = visible
        self._plot_widget.showGrid(x=visible, y=visible, alpha=0.3)

    # ------------------------------------------------------------------
    # Actualización de la gráfica
    # ------------------------------------------------------------------

    def update_plot(
        self,
        timestamps: np.ndarray,
        data: Dict[str, np.ndarray],
        y_range: Optional[Tuple[float, float]] = None,
    ) -> None:
        """
        Actualiza las curvas con los datos de la ventana visible.

        :param timestamps: Array de tiempos relativos.
        :param data: {nombre_señal: array de valores}.
        :param y_range: (y_min, y_max) para autoescalado; ignorado si auto_y=False.
        """
        # Ocultar y limpiar curvas desactivadas.
        for name, curve in self._curves.items():
            if not self._enabled_signals.get(name, True):
                curve.setVisible(False)
                curve.setData([], [])

        if len(timestamps) == 0:
            return

        # Actualizar solo las curvas fijas habilitadas.
        for name in KNOWN_SIGNALS:
            if name not in data or name not in self._curves:
                continue
            values = data[name]
            if not self._enabled_signals.get(name, True):
                continue
            curve = self._curves[name]
            curve.setVisible(True)
            curve.setData(timestamps, values)

        # Configurar rango del eje X.
        t_end = timestamps[-1]
        t_start = t_end - self._window_seconds
        self._plot_widget.setXRange(t_start, t_end, padding=0)

        # Configurar rango del eje Y.
        if self._auto_y and y_range is not None:
            self._plot_widget.setYRange(y_range[0], y_range[1], padding=0)
        elif not self._auto_y and self._manual_y_min is not None:
            self._plot_widget.setYRange(
                self._manual_y_min, self._manual_y_max, padding=0
            )

    def clear(self) -> None:
        """Elimina todas las curvas y reinicia las dos señales fijas."""
        for curve in self._curves.values():
            self._plot_widget.removeItem(curve)
        self._curves.clear()
        self._signal_colors.clear()
        self._enabled_signals.clear()
        self._pan_offset = 0.0
        self.setup_fixed_signals()
