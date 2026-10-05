#pragma once

// 알림을 표시한다. 지금은 시리얼 로그, 나중에 실물 디스플레이로 교체한다.
void displayShow(const char* title, const char* text, const char* priority);

// 표시를 지운다.
void displayClear();

// 지금 표시 중인 문구. 없으면 "". device state 의 display 필드에 그대로 싣는다.
const char* displayCurrent();