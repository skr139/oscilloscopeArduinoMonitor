"""
voltage_converter.py — Conversión ADC → Voltios en la aplicación.

El Arduino envía solo el valor ADC (0–1023). La conversión a voltios se realiza
aquí con parámetros configurables por el usuario:

  Vi (Vinferior)  Voltaje medido cuando ADC = 0
  Vs (Vsuperior)  Voltaje medido cuando ADC = ADC_máximo (1023)

Fórmula:

  Vmedido = Vinferior + (Valor_ADC × (Vsuperior − Vinferior) / Valor_ADC_máximo)

Equivalente en código (ver método adc_to_voltage):

  V = Vi + (ADC × (Vs − Vi) / 1023)
"""

from __future__ import annotations

from dataclasses import dataclass

from signals_config import ADC_MAX, DEFAULT_VI, DEFAULT_VS


@dataclass
class VoltageParams:
    """Parámetros de conversión ADC → voltios."""

    vi: float = DEFAULT_VI   # Vinferior (Vmin)
    vs: float = DEFAULT_VS   # Vsuperior (Vmax)

    @property
    def span(self) -> float:
        """Rango de voltaje: Vsuperior − Vinferior."""
        return self.vs - self.vi


class VoltageConverter:
    """Convierte lecturas ADC a voltios según parámetros del usuario."""

    def __init__(self, params: VoltageParams | None = None) -> None:
        self._params = params or VoltageParams()

    @property
    def params(self) -> VoltageParams:
        return self._params

    def set_params(self, vi: float, vs: float) -> None:
        """Actualiza Vinferior y Vsuperior."""
        self._params = VoltageParams(vi=vi, vs=vs)

    def adc_to_voltage(self, adc: float) -> float:
        """
        Convierte un valor ADC (0–1023) a voltios medidos.

        Vmedido = Vi + (ADC × (Vs − Vi) / ADC_máximo)
        """
        return self._params.vi + (adc * self._params.span / ADC_MAX)

    def formula_description(self) -> str:
        """Texto legible de la fórmula activa."""
        p = self._params
        return (
            f"V = {p.vi:g} + (ADC * ({p.vs:g} - {p.vi:g}) / {ADC_MAX:g})"
        )
