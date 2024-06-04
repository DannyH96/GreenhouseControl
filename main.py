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


# Initialize I2C Bus
i2c_lcd = busio.I2C(board.SCL, board.SDA)

# Set up the LCD
lcd = character_lcd.Character_LCD_I2C(i2c_lcd, LCD_COLUMNS, LCD_ROWS, 0x21)

# Set up CSV file for logging
csv_file = "data.csv"
if not os.path.exists(csv_file):
    with open(csv_file, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Datum", "Temperatur in °C", "Humidity in %", "Lichtlevel", "Lichtbewertung", "Relay Status"])


def getMatrixDisplay():
    # Function to create and configure the matrix display
    serial_interface = spi(port=0, device=1, gpio=noop())
    device = max7219(serial_interface, cascaded=2, block_orientation=90, rotate=0)
    return device


def getSegmentDisplay():
    # Function to create and configure the 7-segment display
    i2c = board.I2C()
    segment = Seg7x4(i2c, address=0x70)
    segment.fill(0)
    return segment


def convertToNumber(data):
    # Function to convert 2 bytes into a decimal number
    return (data[1] + (256 * data[0])) / 1.2


def displayTemperatureAndHumidity(result, segment_display):
    # Display function for temperature and humidity on the segment display
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


def display_on_matrix(device, message):
    with canvas(device) as draw:
        text(draw, (0, 0), message, fill="white", font=proportional(CP437_FONT))


class LightSensor:
    def __init__(self):
        self.DEVICE = 0x5c
        self.ONE_TIME_HIGH_RES_MODE_1 = 0x20

    def readLight(self, bus):
        # Function to read data from the light sensor
        data = bus.read_i2c_block_data(self.DEVICE, self.ONE_TIME_HIGH_RES_MODE_1)
        return convertToNumber(data)

def log_to_csv(data):
    # Function to log data to a CSV file
    with open(csv_file, 'a', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(data)

def log_to_database(data):
    # Function to log to the database
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
            # Get the current time from the NTP server
            current_time = datetime.datetime.now()

            # Read light level and temperature/humidity values
            light_level = lightSensor.readLight(bus)
            tempHum = tempHumSensor.read()

            # Determine light assessment based on light level
            if light_level > OPTIMAL_LIGHT_LEVEL + LIGHT_LEVEL_TOLERANCE:
                light_assessment = "H"  # Too bright
                display_on_matrix(matrixDisplay, "H")
            elif light_level < OPTIMAL_LIGHT_LEVEL - LIGHT_LEVEL_TOLERANCE:
                light_assessment = "D"  # Too dark
                display_on_matrix(matrixDisplay, "D")
            else:
                light_assessment = "G"  # Good
                display_on_matrix(matrixDisplay, "G")

            # Control the relay based on the current time
            if current_time.time() < datetime.time(6, 0) or current_time.time() > datetime.time(20, 0):
                # If before 6:00 AM or after 8:00 PM, turn on the relay
                GPIO.output(RELAY_PIN, GPIO.HIGH)
                relay_state = "ON"
            else:
                # Otherwise, turn off the relay
                GPIO.output(RELAY_PIN, GPIO.LOW)
                relay_state = "OFF"

            # Display temperature and humidity on the 7-segment display and LCD
            displayTemperatureAndHumidity(tempHum, segmentDisplay)

            # Log the current data to the CSV file
            log_data = [
                current_time.strftime("%Y-%m-%d %H:%M:%S"),
                tempHum.temperature,
                tempHum.humidity,
                f"{light_level:.2f}",
                light_assessment,
                relay_state
            ]
            log_to_csv(log_data)
            log_to_database(log_data)

            time.sleep(60)

    except KeyboardInterrupt: # Wenn STRG+C gedrückt wird:
        GPIO.cleanup()
        lcd.clear() # Anzeige auf dem LCD löschen
        lcd.backlight = False # Hintergrundbeleuchtung des LCD ausschalten
        display_on_matrix(matrixDisplay, "") # Anzeige auf der Matrix löschen
        getSegmentDisplay()



if __name__ == "__main__":
    main()
