// Blink — make the little light on your board flash.
//
// LED_BUILTIN is the small light most Arduino-style boards have
// on the board itself. You don't need to wire anything up.

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);   // light on
  delay(500);                        // wait half a second
  digitalWrite(LED_BUILTIN, LOW);    // light off
  delay(500);                        // wait half a second
}
