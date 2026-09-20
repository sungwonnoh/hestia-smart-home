#pragma once

void logInit(unsigned long baud);
void logInfo (const char* tag, const char* fmt, ...) __attribute__((format(printf, 2, 3)));
void logWarn (const char* tag, const char* fmt, ...) __attribute__((format(printf, 2, 3)));
void logError(const char* tag, const char* fmt, ...) __attribute__((format(printf, 2, 3)));
//tag -> 어느 단계에서 나온 로그인지 구분
//tag 예: BOOT, WIFI, MQTT, NTP, SENS
//fmt -> format(출력할 문자열 틀)
