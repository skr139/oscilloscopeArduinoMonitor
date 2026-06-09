"""
performance_config.py — Límites de rendimiento para alta velocidad serial.

A 500000 baud el Arduino puede enviar miles de muestras por segundo.
Estos límites mantienen fluida la gráfica sacrificando procesos secundarios.
"""

# Emisión serial → UI: agrupar muestras antes de notificar a Qt.
SERIAL_BATCH_INTERVAL_S = 0.04      # 40 ms entre lotes (~25 notif./s)
SERIAL_BATCH_MAX_SAMPLES = 1500     # o antes si se llena este lote

# UI: muestras procesadas por cuadro de gráfica como máximo.
MAX_SAMPLES_PER_FRAME = 2500

# Cola pendiente: si se llena, se decima (se conservan las más recientes).
MAX_PENDING_SAMPLES = 30_000

# Gráfica: intervalo del timer (~20 FPS).
PLOT_TIMER_MS = 50

# Estadísticas / tabla: intervalo más lento (proceso secundario).
STATS_TIMER_MS = 1000

# Puntos máximos enviados a PyQtGraph por actualización.
PLOT_MAX_POINTS = 4000

# Puntos usados solo para calcular auto-escala Y.
Y_RANGE_MAX_POINTS = 600
