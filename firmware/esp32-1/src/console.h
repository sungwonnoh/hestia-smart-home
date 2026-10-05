#pragma once

// 한 줄이 완성되면 handler 가 불린다.
typedef void (*ConsoleHandler)(const char* line);

void consoleBegin(ConsoleHandler handler);
void consoleTick();      // loop() 에서 매번. 블로킹하지 않는다