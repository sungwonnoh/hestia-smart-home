#include "console.h"
#include <Arduino.h>

static ConsoleHandler handler_ = nullptr;
static char buf_[64];
static uint8_t len_ = 0;

void consoleBegin(ConsoleHandler handler) {
    handler_ = handler;
    len_ = 0;
    buf_[0] = '\0';
}

void consoleTick() {
    while (Serial.available()) {
        char c = (char)Serial.read();

        if (c == '\n' || c == '\r') {
            if (len_ > 0) {
                if (handler_ != nullptr) handler_(buf_);
                len_ = 0;
                buf_[0] = '\0';
            }
        } else if (len_ < sizeof(buf_) - 1) {
            buf_[len_++] = c;
            buf_[len_] = '\0';
        }
    }
}