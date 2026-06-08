/*
 * OscilloscopeSerialProtocol.ino
 *
 * Ejemplo de firmware Arduino compatible con el monitor serial de Python.
 *
 * PROTOCOLO DE COMUNICACIÓN BIDIRECCIONAL
 * ========================================
 *
 * 1. DATOS DE SENSOR (PC <- Arduino)
 *    Formato por línea:
 *      ADC:<valor>\tVoltios:<valor>\n
 *    Ejemplo:
 *      ADC:5.1\tVoltios:-0.039
 *
 * 2. CAMBIO DE BAUDRATE (PC -> Arduino -> PC)
 *    La PC NO puede cambiar el baudrate unilateralmente. Ambos lados deben
 *    acordar el cambio mediante este protocolo:
 *
 *    a) PC envía (al baudrate ACTUAL):   SET_BAUD=<nuevo_baudrate>\n
 *    b) Arduino:
 *         - Parsea el comando
 *         - Responde ACK en el baudrate ACTUAL:  ACK_BAUD=<nuevo_baudrate>\n
 *         - Espera a que el buffer TX se vacíe (Serial.flush())
 *         - Llama Serial.end() y Serial.begin(nuevo_baudrate)
 *    c) PC cierra su puerto, espera ~300 ms y reabre al nuevo baudrate.
 *
 *    Si el baudrate solicitado no es válido, Arduino responde:
 *      ERR_BAUD=<descripción>\n
 *
 * 3. OTROS COMANDOS (extensible)
 *    PING  -> Arduino responde PONG
 *    STATUS -> Arduino responde STATUS:BAUD=<actual>,UPTIME=<ms>
 *
 * BAUDRATES SOPORTADOS
 * ====================
 * 9600, 19200, 38400, 57600, 115200, 230400, 250000, 500000, 1000000
 * (depende del hardware; AVR puede no soportar 1000000)
 */

// Baudrate inicial — debe coincidir con el valor por defecto de la app Python (500000).
unsigned long currentBaud = 500000;

// Buffer para lectura de comandos entrantes.
String inputBuffer = "";

// Intervalo de envío de datos de prueba (microsegundos).
// Reducir para simular mayor frecuencia de muestreo.
const unsigned long SAMPLE_INTERVAL_US = 2000;  // ~500 Hz
unsigned long lastSampleTime = 0;

// Valores simulados para demostración (reemplazar con lecturas reales del ADC).
float simulatedADC = 512.0;
float simulatedVolts = 0.0;

// ----------------------------------------------------------------
// Lista de baudrates válidos
// ----------------------------------------------------------------
const unsigned long VALID_BAUDS[] = {
  9600, 19200, 38400, 57600, 115200,
  230400, 250000, 500000, 1000000
};
const int NUM_VALID_BAUDS = sizeof(VALID_BAUDS) / sizeof(VALID_BAUDS[0]);

bool isValidBaud(unsigned long baud) {
  for (int i = 0; i < NUM_VALID_BAUDS; i++) {
    if (VALID_BAUDS[i] == baud) return true;
  }
  return false;
}

// ----------------------------------------------------------------
// setup / loop
// ----------------------------------------------------------------
void setup() {
  Serial.begin(currentBaud);
  // En baudrates altos, esperar brevemente a que el UART se estabilice.
  delay(100);
  randomSeed(analogRead(A0));
}

void loop() {
  // Procesar comandos entrantes de la PC.
  readCommands();

  // Enviar muestras de sensor a intervalo fijo.
  unsigned long now = micros();
  if (now - lastSampleTime >= SAMPLE_INTERVAL_US) {
    lastSampleTime = now;
    sendSample();
  }
}

// ----------------------------------------------------------------
// Envío de datos de sensor
// ----------------------------------------------------------------
void sendSample() {
  // Simular señales (reemplazar con analogRead u otro sensor real).
  simulatedADC = 512.0 + 200.0 * sin(millis() / 500.0) + random(-5, 6);
  simulatedVolts = (simulatedADC / 1023.0) * 5.0 - 2.5;

  // Formato requerido por la aplicación Python.
  Serial.print("ADC:");
  Serial.print(simulatedADC, 1);
  Serial.print("\tVoltios:");
  Serial.println(simulatedVolts, 3);
}

// ----------------------------------------------------------------
// Lectura y procesamiento de comandos
// ----------------------------------------------------------------
void readCommands() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputBuffer.length() > 0) {
        processCommand(inputBuffer);
        inputBuffer = "";
      }
    } else {
      inputBuffer += c;
      // Protección contra buffer overflow.
      if (inputBuffer.length() > 64) inputBuffer = "";
    }
  }
}

void processCommand(String cmd) {
  cmd.trim();

  // --- Cambio de baudrate ---
  if (cmd.startsWith("SET_BAUD=")) {
    unsigned long newBaud = cmd.substring(9).toInt();
    handleBaudrateChange(newBaud);
    return;
  }

  // --- Comandos auxiliares ---
  if (cmd == "PING") {
    Serial.println("PONG");
    return;
  }

  if (cmd == "STATUS") {
    Serial.print("STATUS:BAUD=");
    Serial.print(currentBaud);
    Serial.print(",UPTIME=");
    Serial.println(millis());
    return;
  }

  // Comando desconocido (ignorar o reportar).
  Serial.print("ERR_UNKNOWN=");
  Serial.println(cmd);
}

// ----------------------------------------------------------------
// Protocolo de cambio de baudrate
// ----------------------------------------------------------------
void handleBaudrateChange(unsigned long newBaud) {
  if (!isValidBaud(newBaud)) {
    Serial.print("ERR_BAUD=Valor no soportado: ");
    Serial.println(newBaud);
    return;
  }

  if (newBaud == currentBaud) {
    Serial.print("ACK_BAUD=");
    Serial.println(newBaud);
    return;
  }

  // Paso 1: Confirmar en el baudrate ACTUAL antes de cambiar.
  Serial.print("ACK_BAUD=");
  Serial.println(newBaud);

  // Paso 2: Esperar a que la respuesta ACK salga completamente.
  Serial.flush();
  delay(50);

  // Paso 3: Reiniciar UART al nuevo baudrate.
  Serial.end();
  delay(50);
  Serial.begin(newBaud);
  currentBaud = newBaud;

  // La PC detectará el ACK, cerrará su puerto y reabrirá al nuevo baudrate.
}
