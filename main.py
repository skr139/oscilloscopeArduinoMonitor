"""
main.py — Punto de entrada de la aplicación.

Ejecutar con:  python main.py
"""

import atexit
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from main_window import MainWindow

# Referencia global para liberar el puerto si el proceso termina abruptamente.
_app_instance: QApplication | None = None
_window_instance: MainWindow | None = None


def _cleanup_serial() -> None:
    """Garantiza liberación del puerto COM al salir del proceso."""
    global _window_instance
    if _window_instance is not None and _window_instance._serial.is_connected:
        _window_instance._serial.disconnect()


def main() -> None:
    global _app_instance, _window_instance
    # Habilitar DPI alto en pantallas HiDPI.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Osciloscopio Arduino")
    app.setOrganizationName("SKR")

    # Estilo oscuro global.
    app.setStyle("Fusion")
    dark_stylesheet = """
        QMainWindow, QWidget {
            background-color: #12121e;
            color: #e0e0e0;
        }
        QGroupBox {
            border: 1px solid #333355;
            border-radius: 6px;
            margin-top: 8px;
            padding-top: 16px;
            font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }
        QPushButton {
            background-color: #2a2a4a;
            border: 1px solid #444466;
            border-radius: 4px;
            padding: 5px 10px;
            color: #e0e0e0;
        }
        QPushButton:hover {
            background-color: #3a3a6a;
        }
        QPushButton:pressed {
            background-color: #1a1a3a;
        }
        QPushButton:disabled {
            color: #666;
            background-color: #1a1a2a;
        }
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
            background-color: #1e1e36;
            border: 1px solid #444466;
            border-radius: 4px;
            padding: 4px;
            color: #e0e0e0;
        }
        QTableWidget {
            background-color: #1a1a2e;
            gridline-color: #333355;
            border: 1px solid #333355;
        }
        QHeaderView::section {
            background-color: #2a2a4a;
            color: #e0e0e0;
            border: 1px solid #333355;
            padding: 4px;
        }
        QTextEdit {
            background-color: #0d0d1a;
            border: 1px solid #333355;
            color: #a0ffa0;
        }
        QStatusBar {
            background-color: #1a1a2e;
            color: #aaa;
        }
        QScrollArea {
            border: none;
        }
        QCheckBox {
            spacing: 6px;
        }
    """
    app.setStyleSheet(dark_stylesheet)

    atexit.register(_cleanup_serial)

    window = MainWindow()
    _window_instance = window
    window.show()

    exit_code = app.exec()

    _cleanup_serial()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
