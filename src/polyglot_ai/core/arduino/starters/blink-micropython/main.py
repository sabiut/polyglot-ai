# Blink — flash the on-board LED with MicroPython.
#
# Most boards expose the built-in light on a known pin. The names
# below cover the common ones; if your board uses something else,
# change LED_PIN to the right number.

from machine import Pin
from time import sleep

# Pico / Pico W: 25 (Pico) or "LED" (Pico W)
# ESP32 dev board: usually 2
# ESP8266 NodeMCU: usually 2 (active LOW)
LED_PIN = "LED"  # Pico W; change if needed

try:
    led = Pin(LED_PIN, Pin.OUT)
except (TypeError, ValueError):
    # Older Picos and ESP32 use a number, not the string "LED".
    led = Pin(2, Pin.OUT)

while True:
    led.value(1)
    sleep(0.5)
    led.value(0)
    sleep(0.5)
