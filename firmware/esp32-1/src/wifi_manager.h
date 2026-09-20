#pragma once

#include <cstdint>

//WifiState 3가지 상태
enum class WifiState : uint8_t { Disconnected, Connecting, Connected };

class WifiManager {
public:
    WifiManager(const char* ssid, const char* password); //WifiManager 객체 생성시 SSID, PASSWORD 필요

    void begin();                  // setup()에서 1회
    void tick();                   // loop()에서 매번
    bool isConnected() const;

private:
    static const uint32_t CONNECT_TIMEOUT_MS = 10000;   // 시도 포기 기준
    static const uint32_t BACKOFF_MIN_MS     = 1000;
    static const uint32_t BACKOFF_MAX_MS     = 30000;

    void startAttempt();

    const char* ssid_;
    const char* password_;
    WifiState   state_;
    uint32_t    lastAttempt_;      // 마지막 begin() 호출 시각
    uint32_t    backoffMs_;        // 다음 재시도까지 간격
};