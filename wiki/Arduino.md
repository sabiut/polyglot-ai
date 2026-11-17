# Arduino and microcontrollers

Write code for an Arduino, ESP32, Raspberry Pi Pico or similar board,
send it to the board, and see what the board says back — in one window.
Open it from the chip icon in the activity bar or with `Ctrl+Shift+B`.
(The icon is hidden by default: turn it on under **Settings → Panels**.)

The window walks through four steps. When it is wide enough, steps 1–3
are on the left and **See output** stays in view on the right; in a
narrow window everything is one column and rows of buttons wrap instead
of being cut off.

## What you need

| For | Tool | How to get it |
|---|---|---|
| Arduino C++ sketches (`.ino`) | `arduino-cli` | [arduino.github.io/arduino-cli](https://arduino.github.io/arduino-cli/latest/installation/) |
| MicroPython (`main.py`) | `mpremote` | bundled with the app (`pip install --user mpremote` if the chip says it is missing) |
| Finding boards, the serial monitor | `pyserial` | bundled with the app |

The row of chips at the bottom of the window shows which of these were
found. On Linux your user also has to be in the `dialout` group to talk
to a board; the first time a board is found without it, the window shows
the command to run.

## 1. Your project

Pick a starter (Blink, Hello world, a button, a tone…), start a blank
project, or open a folder that already has a sketch in it. The language
comes from the files: `.ino` is Arduino C++, `main.py` MicroPython,
`code.py` CircuitPython.

**Open in Editor** opens the file in the main window's editor; that is
where you edit it. Save before you upload — what is built is the file
on disk.

## 2. Plug in your board

Boards are looked for every few seconds; there is nothing to press.

- **One board** is used without asking.
- **More than one** — a list appears under the "Found 2 boards" line.
  Choose the one to upload to; the choice is kept until that board is
  unplugged.
- **A board that isn't recognised** (many clones show up only as "USB
  Serial") — open **Advanced** and set **Board** yourself.

**Advanced** also lets you set the port by hand, pick the CIRCUITPY
drive, and erase your code from the board. *Automatic* means "use what
was found". What you set there stays set until you change it back, and
the line in step 2 says when it is in effect.

## 3. Upload, or just check

**Upload** builds your code and sends it to the board. **Check code**
only builds it: nothing is sent, and no board has to be plugged in. With
no board, a C++ sketch is checked as if for an Arduino Uno (the feed says
so); Python files are checked for mistakes in how they are written.

While something runs, the big button turns into **Cancel**, and a line
under it counts the time. The board and port can't be changed until it
finishes: an upload always goes to the board its build was made for. The first build for a board compiles the
board's whole support package and can take minutes; later builds take
seconds.

### When something goes wrong

The feed says what happened in plain words, and a card under the buttons
shows the tool's own message with the way out:

| What happened | What you see | What to do |
|---|---|---|
| A mistake in the code | The file and line, and what the compiler said — *"blink.ino, line 9: expected ';' before 'delay'"* | **Go to line 9** opens the file there. **Explain this error** sends the code and the error to the chat. |
| Board support isn't installed | A fresh `arduino-cli` knows no boards. Your code has not been checked yet. | **Install support for …** — see below. |
| A library is missing | The `#include` that couldn't be found | **Install the library for …** — see below. |
| It took too long | The build or upload was stopped after its time limit | Try again; the first build is the slow one. |
| The board didn't answer | Wrong board chosen, a charge-only cable, or a board that needs its BOOT button held | Check the board under **Advanced**, try another cable. |
| The connection is in use | Another serial monitor or the Arduino IDE has the port | Close it and try again. |
| No permission | Your user isn't in `dialout` | Run the command shown, then log out and back in. |

**▸ Show what the tools printed** unfolds everything `arduino-cli` or
`mpremote` printed, line by line as it arrives.

### Installing board support and libraries

Nothing is downloaded without asking. The **Install** button opens a
question that says what will be installed and where it comes from:

- **Board support** (`arduino:avr`, `esp32:esp32`, …) is installed with
  `arduino-cli core install`. Arduino's own packages come from
  `downloads.arduino.cc`; ESP32, ESP8266, Raspberry Pi Pico, Adafruit
  and Teensy packages come from their makers' own package lists. Classic
  Arduino boards are tens of megabytes, ESP32 several hundred.
- **Libraries** are looked up in the Arduino Library Manager by the name
  of the missing header — `Servo.h` → *Servo*, `Adafruit_NeoPixel.h` →
  *Adafruit NeoPixel* — and installed with `arduino-cli lib install`.

When the install finishes, what you had asked for — the check or the
upload — is run again by itself. A download can be cancelled with the
same big button.

## 4. See output

The monitor shows what your board prints with `Serial.println(...)` or
`print(...)`. It starts by itself after a successful upload, and lets go
of the board while the next upload runs.

| Control | What it does |
|---|---|
| **Speed** | Has to match `Serial.begin(...)` in your code. Garbled text usually means it doesn't. |
| **Pause** | Holds the display still so you can read it. The board keeps talking; the button counts what arrived, and **Resume** shows it. |
| **Graph** | Draws the numbers the board prints as lines over the last 300 readings. |
| **Time on each line** | Puts the time each line arrived, to the millisecond, in front of it. |
| **Clear** / **Save…** | Empty the display, or save what is shown to a text file. |
| The box at the bottom | Type a line and press Enter to send it to the board. The list beside it chooses what is sent after your text; most sketches wait for a **New line**. |

### Getting a graph

Print numbers, one reading per line. Several values on a line —
separated by commas, spaces or tabs — become several lines on the graph,
and `name:value` gives a line its name:

```cpp
Serial.print("temp:");
Serial.print(temperature);
Serial.print(",humidity:");
Serial.println(humidity);
```

All series share one scale. A series keeps its colour for as long as it
is on the graph. Point at the graph to read the values at that moment;
lines that aren't numbers ("Sensor ready") stay out of it and still show
in the text.

## Asking the AI

**Ask AI for help** hands the chat what is loaded, the board, the code
and — after a failure — the tools' output and what the window made of
it. The chat can also drive the same steps itself (`arduino_compile`,
`arduino_upload`, …), each with your approval. *Automatically ask AI when
upload fails* under **Advanced** does the hand-over for you; it is not
used for problems the AI can't fix by reading code, such as a board
package that isn't installed.
