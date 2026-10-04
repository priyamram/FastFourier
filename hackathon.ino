#include <SPI.h>
#include <MFRC522.h>

#define SS_PIN 10
#define RST_PIN 9

MFRC522 rfid(SS_PIN, RST_PIN);

void setup() {

  Serial.begin(9600);

  SPI.begin();
  rfid.PCD_Init();

  delay(1000);

  Serial.println("=================================");
  Serial.println("        FAST FOURIER");
  Serial.println("=================================");
  Serial.println("NFC SYSTEM READY");
  Serial.println("Tap your MAROON card...");
  Serial.println();
}


void loop() {

  // Wait for a card
  if (!rfid.PICC_IsNewCardPresent()) {
    return;
  }

  // Read the card
  if (!rfid.PICC_ReadCardSerial()) {
    return;
  }

  // Build the UID string
  String uid = "";

  for (byte i = 0; i < rfid.uid.size; i++) {

    if (rfid.uid.uidByte[i] < 0x10) {
      uid += "0";
    }

    uid += String(
      rfid.uid.uidByte[i],
      HEX
    );

    if (i < rfid.uid.size - 1) {
      uid += ":";
    }
  }

  uid.toUpperCase();


  // Send UID to computer
  Serial.print("CARD_UID:");
  Serial.println(uid);


  // Also display it nicely
  Serial.println();
  Serial.println("---------------------------------");
  Serial.println("CARD DETECTED");
  Serial.print("UID: ");
  Serial.println(uid);
  Serial.println("---------------------------------");
  Serial.println();


  // Stop reading this card
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  delay(2000);
}
