#include <Sabertooth.h>
#include <SoftwareSerial.h>

#define SABERTOOTH_TX_PIN 11
#define SABERTOOTH_ADDRESS 128

SoftwareSerial SWSerial(NOT_A_PIN, SABERTOOTH_TX_PIN);
Sabertooth ST(SABERTOOTH_ADDRESS, SWSerial);

void setup() {
  Serial.begin(115200);
  SWSerial.begin(115200);

  // Wait for Raspberry Pi to be ready
  while (!Serial) {
    delay(100);
  }
}

void loop() {
  if (Serial.available() >= 4) {
    byte address = Serial.read();
    byte command = Serial.read();
    byte value = Serial.read();
    byte checksum = Serial.read();

    byte calculatedChecksum = (address + command + value) & 0x7F;

    // Verify the checksum
    if (calculatedChecksum == checksum) {
      ST.command(command, value);
    } else {
      Serial.print("Checksum error. Received Checksum: ");
      Serial.print(checksum);
      Serial.print(", Calculated Checksum: ");
      Serial.println(calculatedChecksum);
    }
  }
}