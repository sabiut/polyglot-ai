// Button → LED.
//
// Wiring:
//   - One leg of a button to pin 2.
//   - The other leg to GND.
// We turn on the chip's built-in pull-up resistor so we don't need
// any extra parts. Pressing the button connects pin 2 to GND, which
// reads as LOW.

const int BUTTON_PIN = 2;

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  bool pressed = digitalRead(BUTTON_PIN) == LOW;
  digitalWrite(LED_BUILTIN, pressed ? HIGH : LOW);
}
