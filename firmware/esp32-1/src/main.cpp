#include <Arduino.h>
#include "payload.h" 

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("[BOOT] HESTIA node");
}

void loop() {
}