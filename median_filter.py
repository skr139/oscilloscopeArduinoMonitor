"""
median_filter.py — Filtro de mediana móvil para suavizar ruido en el ADC.

Usa collections.deque y statistics.median como solicitó el usuario.
"""

from __future__ import annotations

import statistics
from collections import deque


class MedianFilter:
    """Filtro de mediana con ventana deslizante de tamaño configurable."""

    def __init__(self, maxlen: int = 9, enabled: bool = False) -> None:
        self._maxlen = max(3, maxlen | 1)  # forzar impar >= 3
        self._enabled = enabled
        self._window: deque[float] = deque(maxlen=self._maxlen)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def maxlen(self) -> int:
        return self._maxlen

    def configure(self, maxlen: int, enabled: bool) -> None:
        """Configura tamaño de ventana (impar) y estado activo/inactivo."""
        self._maxlen = max(3, int(maxlen) | 1)
        self._enabled = enabled
        self.reset()

    def reset(self) -> None:
        """Vacía la ventana (p. ej. al activar o cambiar maxlen)."""
        self._window = deque(maxlen=self._maxlen)

    def filter(self, value: float) -> float:
        """
        Aplica filtro mediana al nuevo valor.

        Si está desactivado, devuelve el valor sin modificar.
        """
        if not self._enabled:
            return value

        self._window.append(value)
        return float(statistics.median(self._window))
