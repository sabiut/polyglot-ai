// Play a short tune on a piezo buzzer.
//
// Wiring:
//   - One leg of the piezo to pin 8.
//   - The other leg to GND.

const int SPEAKER_PIN = 8;

// A few note frequencies in Hz. Add more if you want to extend
// the song — middle C is 262.
const int NOTE_C4 = 262;
const int NOTE_E4 = 330;
const int NOTE_G4 = 392;
const int NOTE_C5 = 523;

void setup() {
  // Nothing to set up — tone() handles the pin.
}

void loop() {
  tone(SPEAKER_PIN, NOTE_C4, 200);
  delay(250);
  tone(SPEAKER_PIN, NOTE_E4, 200);
  delay(250);
  tone(SPEAKER_PIN, NOTE_G4, 200);
  delay(250);
  tone(SPEAKER_PIN, NOTE_C5, 400);
  delay(800);
}
