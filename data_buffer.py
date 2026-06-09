"""
data_buffer.py — Almacenamiento eficiente de muestras en tiempo real.

Utiliza arrays NumPy pre-dimensionados que crecen de forma amortizada para
mantener alto rendimiento con grandes volúmenes de datos. Mantiene un historial
completo (hasta un límite configurable) para permitir desplazamiento temporal
(pan) y exportación a CSV.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from median_filter import MedianFilter
from signals_config import ADC_MAX, KNOWN_SIGNALS, SIGNAL_ADC, SIGNAL_VOLTIOS
from voltage_converter import VoltageConverter


# Capacidad inicial y factor de crecimiento del buffer interno.
_INITIAL_CAPACITY = 10_000
_GROWTH_FACTOR = 2


@dataclass
class SignalStats:
    """Estadísticas en tiempo real de una señal."""

    current: float = 0.0
    minimum: float = float("inf")
    maximum: float = float("-inf")
    average: float = 0.0
    sum: float = 0.0
    count: int = 0


@dataclass
class SampleRateTracker:
    """Calcula la frecuencia de muestreo aproximada (Hz)."""

    _timestamps: deque = field(default_factory=deque)
    _window_seconds: float = 2.0
    _max_entries: int = 5000

    def add(self, timestamp: float) -> None:
        self._timestamps.append(timestamp)
        cutoff = timestamp - self._window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        while len(self._timestamps) > self._max_entries:
            self._timestamps.popleft()

    def add_batch(self, last_timestamp: float, count: int) -> None:
        """Registra una ráfaga de muestras usando solo el último timestamp."""
        if count <= 0:
            return
        self.add(last_timestamp)

    @property
    def rate_hz(self) -> float:
        if len(self._timestamps) < 2:
            return 0.0
        duration = self._timestamps[-1] - self._timestamps[0]
        if duration <= 0:
            return 0.0
        return (len(self._timestamps) - 1) / duration


class DataBuffer:
    """
    Buffer thread-safe para series temporales multicanal.

    Cada señal se almacena en un array NumPy paralelo al array de timestamps.
    Los nombres de señal se detectan dinámicamente al recibir la primera muestra.
    """

    def __init__(
        self,
        max_samples: int = 5_000_000,
        converter: VoltageConverter | None = None,
        median_filter: MedianFilter | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._max_samples = max_samples
        self._capacity = _INITIAL_CAPACITY
        self._count = 0
        self._converter = converter or VoltageConverter()
        self._median_filter = median_filter or MedianFilter()

        # Tiempo relativo al primer sample (segundos).
        self._timestamps = np.zeros(self._capacity, dtype=np.float64)
        self._signals: Dict[str, np.ndarray] = {}
        self._signal_stats: Dict[str, SignalStats] = {}
        self._init_known_signals()
        self._start_time: Optional[float] = None
        self._sample_rate_tracker = SampleRateTracker()

        # Buffer de grabación (copia independiente mientras se graba).
        self._recording = False
        self._record_timestamps: List[float] = []
        self._record_signals: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------
    # Propiedades públicas
    # ------------------------------------------------------------------

    @property
    def signal_names(self) -> List[str]:
        with self._lock:
            return list(self._signals.keys())

    @property
    def sample_count(self) -> int:
        with self._lock:
            return self._count

    @property
    def duration(self) -> float:
        """Duración total del historial en segundos."""
        with self._lock:
            if self._count < 2:
                return 0.0
            return float(self._timestamps[self._count - 1] - self._timestamps[0])

    @property
    def sample_rate_hz(self) -> float:
        return self._sample_rate_tracker.rate_hz

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _init_known_signals(self) -> None:
        """Pre-crea únicamente las dos señales fijas: ADC y Voltios."""
        for name in KNOWN_SIGNALS:
            self._signals[name] = np.zeros(self._capacity, dtype=np.float64)
            self._signal_stats[name] = SignalStats()

    # ------------------------------------------------------------------
    # Ingesta de datos
    # ------------------------------------------------------------------

    def set_converter(self, converter: VoltageConverter) -> None:
        """Asigna el convertidor ADC→Voltios y recalcula el historial."""
        with self._lock:
            self._converter = converter
            self._recompute_voltios_locked()

    def set_median_filter(self, maxlen: int, enabled: bool) -> None:
        """Activa/desactiva el filtro mediana sobre el ADC entrante."""
        with self._lock:
            self._median_filter.configure(maxlen, enabled)

    @property
    def median_filter_enabled(self) -> bool:
        return self._median_filter.enabled

    def _process_adc(self, adc_raw: float) -> tuple[float, float]:
        """
        Limita, filtra y convierte un valor ADC.

        :returns: (adc_almacenado, voltios)
        """
        adc = max(0.0, min(ADC_MAX, float(adc_raw)))
        adc = self._median_filter.filter(adc)

        p = self._converter.params
        voltios = p.vi + (adc * p.span / ADC_MAX)
        voltios = max(p.vi, min(p.vs, voltios))

        return adc, voltios

    def _recompute_voltios_locked(self) -> None:
        """Recalcula Voltios con NumPy (rápido, sin bloquear tanto la UI)."""
        if self._count == 0:
            return

        adc_arr = self._signals[SIGNAL_ADC][: self._count]
        p = self._converter.params
        volt_arr = p.vi + (adc_arr * p.span / ADC_MAX)
        volt_arr = np.clip(volt_arr, p.vi, p.vs)
        self._signals[SIGNAL_VOLTIOS][: self._count] = volt_arr
        self._rebuild_stats_for_signal(SIGNAL_VOLTIOS)

    def _rebuild_stats_for_signal(self, name: str) -> None:
        """Reconstruye estadísticas de una señal desde el buffer."""
        if self._count == 0:
            self._signal_stats[name] = SignalStats()
            return

        arr = self._signals[name][: self._count]
        valid = arr[~np.isnan(arr)]
        if len(valid) == 0:
            self._signal_stats[name] = SignalStats()
            return

        stats = SignalStats()
        stats.current = float(valid[-1])
        stats.minimum = float(valid.min())
        stats.maximum = float(valid.max())
        stats.sum = float(valid.sum())
        stats.count = len(valid)
        stats.average = stats.sum / stats.count
        self._signal_stats[name] = stats

    def add_samples_batch(
        self,
        samples: List[Tuple[float, float]],
    ) -> int:
        """
        Añade un lote de muestras en una sola operación (mucho más eficiente).

        :param samples: Lista de (adc, timestamp_absoluto).
        :returns: Cantidad de muestras añadidas.
        """
        if not samples:
            return 0

        with self._lock:
            added = 0

            for adc_raw, recv_ts in samples:
                if self._start_time is None:
                    self._start_time = recv_ts

                relative_t = recv_ts - self._start_time
                adc, voltios = self._process_adc(adc_raw)

                if self._count >= self._capacity:
                    self._grow()

                idx = self._count
                self._timestamps[idx] = relative_t
                self._signals[SIGNAL_ADC][idx] = adc
                self._signals[SIGNAL_VOLTIOS][idx] = voltios

                self._count += 1
                added += 1

                if self._recording:
                    self._record_timestamps.append(recv_ts)
                    self._record_signals.setdefault(SIGNAL_ADC, []).append(adc)
                    self._record_signals.setdefault(SIGNAL_VOLTIOS, []).append(
                        self._signals[SIGNAL_VOLTIOS][idx]
                    )

            if added:
                self._sample_rate_tracker.add_batch(
                    self._timestamps[self._count - 1], added
                )
                self._rebuild_stats_for_signal(SIGNAL_ADC)
                self._rebuild_stats_for_signal(SIGNAL_VOLTIOS)

            return added

    def add_sample(self, values: Dict[str, float], recv_timestamp: Optional[float] = None) -> None:
        """
        Añade una muestra. Solo se requiere ADC; Voltios se calcula en la app.

        :param values: Diccionario con al menos {ADC: valor}.
        :param recv_timestamp: Timestamp absoluto de recepción (time.time()).
        """
        if SIGNAL_ADC not in values:
            return

        now = recv_timestamp if recv_timestamp is not None else time.time()
        self.add_samples_batch([(float(values[SIGNAL_ADC]), now)])

    def _update_stats(self, name: str, value: float) -> None:
        """Actualiza estadísticas incrementales de una señal."""
        stats = self._signal_stats[name]
        if np.isnan(value):
            return
        stats.current = value
        stats.minimum = min(stats.minimum, value)
        stats.maximum = max(stats.maximum, value)
        stats.sum += value
        stats.count += 1
        stats.average = stats.sum / stats.count

    def _grow(self) -> None:
        """Duplica la capacidad de todos los arrays (crecimiento amortizado)."""
        new_cap = min(self._capacity * _GROWTH_FACTOR, self._max_samples)
        if new_cap == self._capacity:
            # Buffer lleno: desplazar datos (descartar la mitad más antigua).
            half = self._count // 2
            self._timestamps[: half] = self._timestamps[half: self._count]
            for name, arr in self._signals.items():
                arr[: half] = arr[half: self._count]
            self._count = half
            return

        new_ts = np.zeros(new_cap, dtype=np.float64)
        new_ts[: self._count] = self._timestamps[: self._count]
        self._timestamps = new_ts

        for name, arr in self._signals.items():
            new_arr = np.zeros(new_cap, dtype=np.float64)
            new_arr[: self._count] = arr[: self._count]
            self._signals[name] = new_arr

        self._capacity = new_cap

    # ------------------------------------------------------------------
    # Consulta de datos para visualización
    # ------------------------------------------------------------------

    def get_window(
        self,
        window_seconds: float,
        pan_offset: float = 0.0,
        max_points: int = 5000,
    ) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        """
        Devuelve los datos visibles en la ventana temporal solicitada.

        :param window_seconds: Ancho de la ventana en segundos.
        :param pan_offset: Desplazamiento hacia atrás desde el final (segundos).
        :param max_points: Máximo de puntos a devolver (decimación automática).
        :returns: (timestamps, {señal: valores}) recortados y opcionalmente decimados.
        """
        with self._lock:
            if self._count == 0:
                return np.array([]), {}

            ts = self._timestamps[: self._count]
            t_end = ts[-1] - pan_offset
            t_start = t_end - window_seconds

            # Índices del rango visible.
            mask = (ts >= t_start) & (ts <= t_end)
            visible_ts = ts[mask]

            if len(visible_ts) == 0:
                return np.array([]), {}

            # Decimación para mantener fluidez en la gráfica.
            step = max(1, len(visible_ts) // max_points)
            visible_ts = visible_ts[::step]

            result: Dict[str, np.ndarray] = {}
            full_mask_indices = np.where(mask)[0][::step]
            for name, arr in self._signals.items():
                result[name] = arr[full_mask_indices]

            return visible_ts, result

    def get_stats(self) -> Dict[str, SignalStats]:
        """Devuelve una copia de las estadísticas de cada señal."""
        with self._lock:
            return {k: SignalStats(**vars(v)) for k, v in self._signal_stats.items()}

    def get_y_range(
        self,
        window_seconds: float,
        pan_offset: float = 0.0,
        enabled_signals: Optional[List[str]] = None,
    ) -> Tuple[float, float]:
        """
        Calcula min/max del eje Y para la ventana visible.

        Si Voltios y ADC están activos a la vez, escala solo Voltios: el ADC
        (0–1023) distorsionaría el eje y mostraría valores como 450 en voltaje.
        """
        _, data = self.get_window(window_seconds, pan_offset, max_points=50_000)
        if not data:
            return 0.0, 1.0

        scale_signals = list(enabled_signals) if enabled_signals else list(KNOWN_SIGNALS)
        if (
            enabled_signals
            and SIGNAL_VOLTIOS in enabled_signals
            and SIGNAL_ADC in enabled_signals
        ):
            scale_signals = [SIGNAL_VOLTIOS]

        mins, maxs = [], []
        p = self._converter.params

        for name in scale_signals:
            arr = data.get(name)
            if arr is None:
                continue
            valid = arr[~np.isnan(arr)]
            if len(valid) == 0:
                continue

            v_min = float(valid.min())
            v_max = float(valid.max())

            # Limitar a rango físico esperado (evita picos espurios en el eje Y).
            if name == SIGNAL_VOLTIOS:
                v_min = max(p.vi, v_min)
                v_max = min(p.vs, v_max)
            elif name == SIGNAL_ADC:
                v_min = max(0.0, v_min)
                v_max = min(ADC_MAX, v_max)

            mins.append(v_min)
            maxs.append(v_max)

        if not mins:
            return 0.0, 1.0

        y_min, y_max = min(mins), max(maxs)
        if y_min >= y_max:
            y_max = y_min + 0.1

        margin = (y_max - y_min) * 0.05 or 0.1
        return y_min - margin, y_max + margin

    # ------------------------------------------------------------------
    # Grabación y exportación
    # ------------------------------------------------------------------

    def start_recording(self) -> None:
        with self._lock:
            self._recording = True
            self._record_timestamps.clear()
            self._record_signals.clear()

    def stop_recording(self) -> None:
        with self._lock:
            self._recording = False

    def export_recording(self) -> Tuple[List[float], Dict[str, List[float]]]:
        """Devuelve los datos grabados durante la sesión de grabación."""
        with self._lock:
            return list(self._record_timestamps), {k: list(v) for k, v in self._record_signals.items()}

    def export_all(self) -> Tuple[np.ndarray, Dict[str, np.ndarray], List[float]]:
        """
        Exporta todo el historial almacenado.

        :returns: (timestamps relativos, señales, timestamps absolutos estimados)
        """
        with self._lock:
            ts = self._timestamps[: self._count].copy()
            signals = {k: v[: self._count].copy() for k, v in self._signals.items()}
            if self._start_time is not None:
                abs_ts = [self._start_time + t for t in ts]
            else:
                abs_ts = list(ts)
            return ts, signals, abs_ts

    def clear(self) -> None:
        """Reinicia el buffer y las estadísticas."""
        with self._lock:
            self._count = 0
            self._start_time = None
            self._signals.clear()
            self._signal_stats.clear()
            self._init_known_signals()
            self._sample_rate_tracker = SampleRateTracker()
            self._record_timestamps.clear()
            self._record_signals.clear()
