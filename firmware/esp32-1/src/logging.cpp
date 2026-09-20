#include "logging.h"
#include <Arduino.h>
#include <cstdarg>
#include <cstdio>

//실제 출력 함수: logInfo/logWarn/logError 내부에서 emit 호출
//vsnpringf-> 인자를 va_list로 받아 문자열 출력
static void emit(const char* level, const char* tag,
                 const char* fmt, va_list args) {
    char body[192];
    vsnprintf(body, sizeof(body), fmt, args);
    Serial.printf("[%8lu] %-5s %s %s\n", millis(), tag, level, body);
}

//serial 켜기(serial 포트 열기)
//setup()에서 딱 한 번 호출
void logInit(unsigned long baud) {
    Serial.begin(baud);
    delay(1000);            // USB 시리얼이 열릴 시간
    Serial.println();
}

//va_list-> 가변 인자(...)의 주소를 가리키는 커서
//va_start-> 가변 인자의 시작 주소가 fmt 다음이라는 것을 나타냄(arg의 시작 위치 설정)
//va_end-> 커서 정리
void logInfo(const char* tag, const char* fmt, ...) {
    va_list args;
    va_start(args, fmt);
    emit(" ", tag, fmt, args);
    va_end(args);
}

void logWarn(const char* tag, const char* fmt, ...) {
    va_list args;
    va_start(args, fmt);
    emit("!", tag, fmt, args);
    va_end(args);
}

void logError(const char* tag, const char* fmt, ...) {
    va_list args;
    va_start(args, fmt);
    emit("X", tag, fmt, args);
    va_end(args);
}