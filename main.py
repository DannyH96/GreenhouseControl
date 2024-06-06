import datetime
import os
import time
import csv

import board
import RPi.GPIO as GPIO
import busio
import smbus
import adafruit_character_lcd.character_lcd_i2c as character_lcd
from adafruit_ht16k33.segments import Seg7x4
from luma.core.legacy import text
from luma.core.legacy.font import proportional, CP437_FONT

from luma.core.interface.serial import spi, noop
from luma.led_matrix.device import max7219
from luma.core.render import canvas
from dht11 import DHT11
from socket import AF_INET, SOCK_DGRAM
import sqlite3

LCD_COLUMNS = 16
LCD_ROWS = 2
LIGHT_LEVEL_TOLERANCE = 5000
OPTIMAL_LIGHT_LEVEL = 40000
RELAY_PIN = 21

# Datenbankverbindung aufbauen
# Datenbanktabelle "messwerte" erstellen mit den Attributen
conn = sqlite3.connect('data.db')
cursor = conn.cursor()
cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS messwerte (
    id INTEGER PRIMARY KEY,
    Datum TEXT NOT NULL,
    Temperatur REAL,
    Luftfeuchtigkeit REAL,
    Lichtlevel TEXT,
    Lichtbewertung TEXT,
    Relaystatus TEXT
    )
    """
)
conn.commit()


# Initilisierung des I2C-Bus für das LCD-Display
i2c_lcd = busio.I2C(board.SCL, board.SDA)

# LCD Display initialisieren
lcd = character_lcd.Character_LCD_I2C(i2c_lcd, LCD_COLUMNS, LCD_ROWS, 0x21)

# CSV-Datei mit den Headern erstellen, wenn sie noch nicht existiert
csv_file = "data.csv"
if not os.path.exists(csv_file):
    with open(csv_file, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Datum", "Temperatur in °C", "Humidity in %", "Lichtlevel", "Lichtbewertung", "Relay Status"])


# Funktion, um das Matrix-Display zu erstellen und zu konfigurieren
def getMatrixDisplay():
    serial_interface = spi(port=0, device=1, gpio=noop())
    device = max7219(serial_interface, cascaded=2, block_orientation=90, rotate=0)
    return device


# Funktion, um das 7-Segment-Display zu initialisieren
def getSegmentDisplay():
    i2c = board.I2C()
    segment = Seg7x4(i2c, address=0x70)
    segment.fill(0)
    return segment


# Funktion, um die Lichtwerte in eine Zahl umzuwandeln
def convertToNumber(data):
    return (data[1] + (256 * data[0])) / 1.2

# Funktion, um Temperatur und Luftfeuchtigkeit auf dem 7-Segment-Display und LCD anzuzeigen
def displayTemperatureAndHumidity(result, segment_display):
    temperature = round(result.temperature)
    humidity = round(result.humidity)
    print(f"Temperature: {temperature}°C")
    print(f"Humidity: {humidity}%")
    lcd.message = f"Temp: {temperature}C\nrH: {humidity}%"
    segment_display[0] = str(temperature // 10)
    segment_display[1] = str(temperature % 10)
    segment_display[2] = str(humidity // 10)
    segment_display[3] = str(humidity % 10)
    segment_display.show()


# Funktion, um eine Nachricht auf der Matrix anzuzeigen
def display_on_matrix(device, message):
    with canvas(device) as draw:
        text(draw, (0, 0), message, fill="white", font=proportional(CP437_FONT))


class LightSensor:
    def __init__(self):
        self.DEVICE = 0x5c
        self.ONE_TIME_HIGH_RES_MODE_1 = 0x20

    # Funktion, um den Lichtwert zu lesen
    def readLight(self, bus):
        data = bus.read_i2c_block_data(self.DEVICE, self.ONE_TIME_HIGH_RES_MODE_1)
        return convertToNumber(data)

# Funktion, um die Messwerte in eine CSV-Datei zu schreiben
def log_to_csv(data):
    with open(csv_file, 'a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(data)

# Funktion, um die Messwerte in die Datenbank zu schreiben
def log_to_database(data):
    cursor.execute(
        """
        INSERT INTO messwerte (Datum, Temperatur, Luftfeuchtigkeit, Lichtlevel, Lichtbewertung, Relaystatus)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        data
    )
    conn.commit()


def main():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(RELAY_PIN, GPIO.OUT)
    GPIO.output(RELAY_PIN, GPIO.LOW)

    if GPIO.RPI_REVISION == 1:
        bus = smbus.SMBus(0)
    else:
        bus = smbus.SMBus(1)

    lightSensor = LightSensor()
    tempHumSensor = DHT11(pin=4)
    segmentDisplay = getSegmentDisplay()
    matrixDisplay = getMatrixDisplay()

    try:
        while True:
            # Aktuelle Zeit holen
            current_time = datetime.datetime.now()
            # Entscheiden, ob es Tag ist, wenn Tag dann is_daytime = True
            if (current_time.hour >= 6) or (current_time.hour < 18):
                is_daytime = True
            else:
                is_daytime = False

            # Lichtlevel und Temperatur bzw. Luftfeuchtigkeit auslesen
            light_level = lightSensor.readLight(bus)

            tempHum = tempHumSensor.read()
            # Messwerte aus DHT11 auslesen, bis sie gültig sind
            while not tempHum.is_valid():
                tempHum = tempHumSensor.read()

            needs_light = False

            # Lichtbewertung basierend auf Lichtlevel bestimmen
            # und Anzeige auf der Matrix aktualisieren
            if light_level > OPTIMAL_LIGHT_LEVEL + LIGHT_LEVEL_TOLERANCE:
                light_assessment = "H"  # zu Hell
                display_on_matrix(matrixDisplay, "H")
                needs_light = False
            elif light_level < OPTIMAL_LIGHT_LEVEL - LIGHT_LEVEL_TOLERANCE:
                light_assessment = "D"  # zu Dunkel
                display_on_matrix(matrixDisplay, "D")
                needs_light = True
            else:
                light_assessment = "G"  # optimal bzw. gut
                display_on_matrix(matrixDisplay, "G")

            # Relais schalten basierend auf Tageszeit und Lichtbewertung
            if is_daytime and needs_light:
                GPIO.output(RELAY_PIN, GPIO.LOW)
                relay_state = "ON"
            else:
                GPIO.output(RELAY_PIN, GPIO.HIGH)
                relay_state = "OFF"


            # Temperatur und Luftfeuchtigkeit auf dem 7-Segment-Display und LCD anzeigen
            displayTemperatureAndHumidity(tempHum, segmentDisplay)


            log_data = [
                current_time.strftime("%Y-%m-%d %H:%M:%S"),
                tempHum.temperature,
                tempHum.humidity,
                f"{light_level:.2f}",
                light_assessment,
                relay_state
            ]
            log_to_csv(log_data) # Messwerte in die CSV-Datei schreiben
            log_to_database(log_data) # Messwerte in die Datenbank schreiben

            time.sleep(60)

    except KeyboardInterrupt: # Wenn STRG+C gedrückt wird:
        GPIO.cleanup() # GPIO-Pins zurücksetzen
        lcd.clear() # Anzeige auf dem LCD löschen
        lcd.backlight = False # Hintergrundbeleuchtung des LCD ausschalten
        display_on_matrix(matrixDisplay, "") # Anzeige auf der Matrix löschen
        getSegmentDisplay() # 7-Segment-Display löschen



if __name__ == "__main__":
    main()
