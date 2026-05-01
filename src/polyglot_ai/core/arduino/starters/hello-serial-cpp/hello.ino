// Hello, world! — print a message every second.
//
// After uploading, open the Serial Monitor (the magnifying-glass
// icon, or "What's it saying?" in the Arduino panel) at 9600 baud
// to see the messages.

void setup() {
  Serial.begin(9600);
}

void loop() {
  Serial.println("Hello from your Arduino!");
  delay(1000);
}
