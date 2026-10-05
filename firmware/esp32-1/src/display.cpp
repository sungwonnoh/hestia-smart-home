#include "display.h"
#include "logging.h"
#include <cstring>

static char current_[128] = {0};

void displayShow(const char* title, const char* text, const char* priority) {
    strncpy(current_, text, sizeof(current_) - 1);
    current_[sizeof(current_) - 1] = '\0';

    if (strcmp(priority, "health") == 0) {
        logError("DISP", "[%s] %s | %s", priority, title, text);
    } else if (strcmp(priority, "high") == 0) {
        logWarn("DISP", "[%s] %s | %s", priority, title, text);
    } else {
        logInfo("DISP", "[%s] %s | %s", priority, title, text);
    }
}

void displayClear() {
    if (current_[0] != '\0') {
        logInfo("DISP", "cleared");
    }
    current_[0] = '\0';
}

const char* displayCurrent() {
    return current_;
}