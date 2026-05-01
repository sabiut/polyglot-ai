# Blink — flash the on-board LED with CircuitPython.
#
# CircuitPython knows about board.LED on every board that has one,
# so this works the same on the Pico, Circuit Playground Express,
# Feather M4, and others.

import board
import digitalio
import time

led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT

while True:
    led.value = True
    time.sleep(0.5)
    led.value = False
    time.sleep(0.5)
