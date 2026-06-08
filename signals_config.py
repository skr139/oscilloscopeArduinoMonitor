"""
signals_config.py — Definición de las señales fijas del osciloscopio.

El firmware Arduino envía exactamente dos canales por línea:
  ADC:<adc/100>\tVoltios:<voltaje>

El valor ADC en serie viene escalado (adc/100); la app lo reconstruye a 0–1023.
"""

SIGNAL_ADC = "ADC"
SIGNAL_VOLTIOS = "Voltios"

KNOWN_SIGNALS = (SIGNAL_ADC, SIGNAL_VOLTIOS)

# Arduino envía adc/100.0 → multiplicar para obtener el ADC real (0–1023).
ADC_SCALE_FACTOR = 100.0
ADC_MAX = 1023.0

# Etiquetas amigables en la interfaz.
SIGNAL_LABELS = {
    SIGNAL_ADC: "ADC (0–1023)",
    SIGNAL_VOLTIOS: "Voltios (V)",
}
