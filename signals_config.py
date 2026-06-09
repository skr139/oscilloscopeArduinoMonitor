"""
signals_config.py — Definición de las señales fijas del osciloscopio.

El firmware Arduino envía solo el valor ADC (0–1023) por línea.
La app calcula Voltios localmente con voltage_converter.py.
"""

SIGNAL_ADC = "ADC"
SIGNAL_VOLTIOS = "Voltios"

KNOWN_SIGNALS = (SIGNAL_ADC, SIGNAL_VOLTIOS)

ADC_MAX = 1023.0

# Valores por defecto de la escala de voltaje.
DEFAULT_VI = 0.0
DEFAULT_VS = 5.0

# Margen derecho por defecto en la gráfica (segundos de espacio vacío).
DEFAULT_RIGHT_MARGIN = 1.0

# Etiquetas amigables en la interfaz.
SIGNAL_LABELS = {
    SIGNAL_ADC: "ADC (0–1023)",
    SIGNAL_VOLTIOS: "Voltios (V)",
}
