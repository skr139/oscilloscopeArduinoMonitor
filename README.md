# Osciloscopio Arduino — Monitor Serial en Tiempo Real

Aplicación de escritorio en Python que funciona como alternativa avanzada al Serial Plotter de Arduino. Permite visualizar, grabar, analizar y controlar datos provenientes de un Arduino vía puerto serial con alto rendimiento.

## Características

- **Detección automática** de puertos COM
- **Baudrate por defecto:** 500 000 (configurable)
- **Gráfica en tiempo real** con PyQtGraph (30+ FPS)
- **Señales dinámicas** detectadas automáticamente (`ADC`, `Voltios`, etc.)
- **Ventana temporal** configurable: 1 s, 5 s, 10 s, 30 s, 1 min, 5 min o valor personalizado
- **Pan/zoom** horizontal y vertical con historial completo
- **Autoescalado** y escala manual del eje Y
- **Estadísticas en vivo:** valor actual, mín, máx, promedio, frecuencia de muestreo
- **Grabación y exportación** a CSV con timestamp
- **Comunicación bidireccional** con protocolo de cambio de baudrate coordinado
- **Consola serial** para depuración

---

## Instalación

### Requisitos

- Python 3.10 o superior
- Windows / Linux / macOS

### Pasos

```bash
# 1. Clonar o copiar el proyecto
cd oscilloscopeArduinoMonitor

# 2. Crear entorno virtual (recomendado)
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Ejecutar la aplicación
python main.py
```

---

## Uso rápido

1. Conecte el Arduino y cargue el sketch de ejemplo (`arduino_example/`).
2. Abra la aplicación con `python main.py`.
3. Seleccione el puerto COM y pulse **Conectar**.
4. Las señales aparecerán automáticamente en la gráfica.
5. Use los controles de tiempo, zoom y pan para explorar el historial.
6. Pulse **Grabar** para capturar datos y **Exportar** para guardar CSV.

---

## Formato de datos (Arduino → PC)

Cada línea representa una muestra completa:

```
ADC:5.1\tVoltios:-0.039
```

En general:

```
<NombreSeñal>:<valor>\t<NombreSeñal2>:<valor2>
```

La aplicación detecta los nombres de señal dinámicamente y asigna un color a cada una.

---

## Protocolo de cambio de baudrate (bidireccional)

> **IMPORTANTE:** Arduino no puede cambiar su velocidad solo porque la PC cambie el baudrate del puerto. Ambos deben coordinarse mediante este protocolo.

### Secuencia

```
PC (baudrate actual)  ──►  SET_BAUD=115200
Arduino               ──►  ACK_BAUD=115200    (en baudrate actual)
Arduino               ──►  Serial.end(); Serial.begin(115200);
PC                    ──►  Cierra puerto, espera 300 ms, reabre a 115200
```

### Implementación en Arduino

Ver `arduino_example/OscilloscopeSerialProtocol/OscilloscopeSerialProtocol.ino`.

### Uso desde la aplicación

1. Conecte al baudrate actual.
2. En el panel **Comandos Serial**, seleccione el nuevo baudrate.
3. Pulse **Cambiar baudrate**.
4. La aplicación envía `SET_BAUD`, espera `ACK_BAUD`, y reconecta automáticamente.

### Comandos adicionales soportados por el ejemplo Arduino

| Comando  | Respuesta                      |
|----------|--------------------------------|
| `PING`   | `PONG`                         |
| `STATUS` | `STATUS:BAUD=<n>,UPTIME=<ms>`  |

---

## Arquitectura del proyecto

```
oscilloscopeArduinoMonitor/
├── main.py                  # Punto de entrada
├── main_window.py           # Interfaz gráfica principal
├── serial_manager.py        # Comunicación serial (hilo independiente)
├── data_buffer.py           # Almacenamiento eficiente de muestras
├── plot_manager.py          # Visualización PyQtGraph
├── command_manager.py       # Protocolo de comandos bidireccional
├── requirements.txt
├── README.md
└── arduino_example/
    └── OscilloscopeSerialProtocol/
        └── OscilloscopeSerialProtocol.ino
```

### Descripción de cada módulo

#### `main.py`
Punto de entrada. Configura la aplicación Qt con estilo oscuro y lanza la ventana principal.

#### `serial_manager.py`
- Detecta puertos COM disponibles (`pyserial`).
- Abre/cierra la conexión serial.
- Lee datos en un `QThread` independiente para no bloquear la GUI.
- Parsea líneas con formato `Nombre:valor`.
- Emite señales Qt con datos parseados, líneas crudas y errores.

#### `data_buffer.py`
- Almacena muestras en arrays NumPy con crecimiento amortizado.
- Detecta señales dinámicamente.
- Calcula estadísticas incrementales (min, max, promedio).
- Estima frecuencia de muestreo (Hz).
- Soporta ventana temporal con pan y decimación para rendimiento.
- Gestiona grabación y exportación.

#### `plot_manager.py`
- Gráfica principal con PyQtGraph.
- Curvas multicanal con colores automáticos.
- Zoom/pan en ejes X e Y.
- Autoescalado y escala manual.
- Cuadrícula configurable.

#### `command_manager.py`
- Envío de comandos de texto al Arduino.
- Protocolo de cambio de baudrate con timeout y reconexión automática.
- Manejo de respuestas `ACK_BAUD` / `ERR_BAUD`.

#### `main_window.py`
- Integra todos los módulos en una interfaz con paneles:
  - Conexión serial
  - Control de tiempo (eje X)
  - Control de escala (eje Y)
  - Señales (checkboxes)
  - Estadísticas en tabla
  - Grabación y exportación CSV
  - Comandos y cambio de baudrate
  - Consola serial

---

## Rendimiento

| Aspecto | Estrategia |
|---------|-----------|
| Lectura serial | Hilo `QThread` dedicado |
| Almacenamiento | Arrays NumPy con crecimiento ×2 |
| Actualización gráfica | `QTimer` a ~30 FPS |
| Decimación | Máx. 5000 puntos por actualización |
| Buffer máximo | 5 000 000 muestras (configurable) |

---

## Exportación CSV

Formato del archivo exportado:

```csv
timestamp,ADC,Voltios
1717862400.123,512.3,-0.039
1717862400.125,513.1,-0.037
```

- **Exportar grabación:** solo datos capturados durante la sesión de grabación.
- **Exportar historial:** todos los datos almacenados en el buffer.

---

## Licencia

Proyecto de uso educativo y de laboratorio. Libre para modificar y distribuir.
