# Read the on-chip temperature sensor and print it once a second.
#
# Pico/Pico W: ADC channel 4 is wired to the internal sensor.
# ESP32: a slightly different formula — see the branch below.

import sys
import time
from machine import ADC


def read_pico() -> float:
    sensor = ADC(4)
    raw = sensor.read_u16() * 3.3 / 65535
    # Datasheet formula for the Pico's on-chip sensor.
    return 27 - (raw - 0.706) / 0.001721


def read_esp32() -> float:
    # ESP32's internal sensor isn't very accurate but it works.
    import esp32

    return esp32.raw_temperature()  # already °C-ish on most boards


PLATFORM = sys.platform  # "rp2", "esp32", ...

while True:
    if PLATFORM == "rp2":
        c = read_pico()
    elif PLATFORM == "esp32":
        c = read_esp32()
    else:
        print("This starter expects a Pico or ESP32.")
        break
    print("Temperature:", round(c, 1), "°C")
    time.sleep(1)
