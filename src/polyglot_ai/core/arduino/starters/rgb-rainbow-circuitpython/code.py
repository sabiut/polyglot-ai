# Rainbow lights on the Circuit Playground Express / Feather M4.
#
# This walks the colour wheel slowly. If you want it faster, lower
# the ``time.sleep`` value at the bottom.

import time

from rainbowio import colorwheel

try:
    # Circuit Playground Express has 10 NeoPixels built in.
    from adafruit_circuitplayground import cp

    pixels = cp.pixels
    pixels.brightness = 0.2
    num = 10
except ImportError:
    # Feather M4 + a NeoPixel strip wired to D5 (change as needed).
    import board
    import neopixel

    num = 8
    pixels = neopixel.NeoPixel(board.D5, num, brightness=0.2, auto_write=False)

offset = 0
while True:
    for i in range(num):
        pixels[i] = colorwheel((offset + i * 256 // num) & 255)
    pixels.show()
    offset = (offset + 1) & 255
    time.sleep(0.02)
